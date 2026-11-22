#!/usr/bin/env python3
"""Synthetic offline capability-contract and archive-binding mutation tests."""
import argparse
import copy
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest

TOOLS = Path(__file__).resolve().parents[2] / 'src/release-tools'
sys.path.insert(0, str(TOOLS))
import capabilities as cap
import contract_validate as cv

SCRATCH = None


def sha(data):
    return hashlib.sha256(data).hexdigest()


def classic(role):
    config = cv.canonical({'architecture': 'amd64', 'os': 'linux',
                           'rootfs': {'type': 'layers', 'diff_ids': []},
                           'config': {'Labels': {'synthetic-role': role}}})
    digest = sha(config)
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode='w', format=tarfile.USTAR_FORMAT) as tar:
        for name, raw in [(digest + '.json', config), ('manifest.json', cv.canonical([
                {'Config': digest + '.json', 'RepoTags': ['synthetic/' + role + ':test'], 'Layers': []}]))]:
            info = tarfile.TarInfo(name)
            info.size = len(raw)
            info.mtime = 0
            info.mode = 0o644
            tar.addfile(info, io.BytesIO(raw))
    return data.getvalue(), 'sha256:' + digest


def fixture(include_full=True):
    """Invented bytes/identities/budgets, never source release claims."""
    pins = {'release_profile': 'pack', 'tei': {'variants': {card: {
        'experimental': card in {'turing', 'blackwell100', 'blackwell120'},
        'digest': 'sha256:' + sha(('source-' + card).encode())}
        for card in sorted(cap.GPU_CLASSES)}}, 'runtime': {},
        'builder': {'qdrant': {'image': 'synthetic/qdrant@sha256:' + 'b' * 64}}, 'models': {}}
    manifest = {'format': 'deep32-capability-manifest', 'format_version': 2,
        'release': '0.1.0-alpha.3', 'concept_doi': '10.5281/zenodo.24272647',
        'code_licence': 'Apache-2.0', 'documentation_licence': 'CC-BY-4.0',
        'jina_model_licence': 'CC-BY-NC-4.0', 'source_commit': 'c' * 40,
        'pins_sha256': '0' * 64, 'image_index_sha256': '1' * 64,
        'hardware_classes': sorted(cap.GPU_CLASSES), 'previous_images': 'retain-until-user-removes',
        'legacy_policy': 'refuse-unsupported-version', 'bundles': []}
    payloads, index = {}, []

    def bundle(name, kind, card=None, target=None):
        b = {'id': name, 'kind': kind, 'hardware_class': card, 'corresponds_to': target,
             'requires': {'core': [], 'embedder': ['core'], 'ingestion': ['core'], 'full': ['core', 'ingestion'],
                          'corresponding-source-build': []}[kind],
             'members': [], 'images': [], 'models': [], 'storage_budgets': []}
        raw = payloads[name] = {}
        def add(path, category, content):
            raw[path] = content
            b['members'].append({'path': path, 'category': category, 'size': len(content), 'sha256': sha(content)})
        add('licences/test.txt', 'licence', b'Synthetic fixture licence evidence.\n')
        add('evidence/storage.json', 'provenance', b'{"synthetic":true,"measured":false}\n')
        if kind == 'corresponding-source-build':
            add('source/test.py', 'source', b'# synthetic source\n')
            add('locks/test.lock', 'lock', b'synthetic==1 --hash=sha256:' + b'd' * 64 + b'\n')
            add('build/recipe.txt', 'build', b'synthetic recipe and toolchain closure\n')
        else:
            if kind != 'embedder':
                add('kit/runtime.py', 'runtime', b'# synthetic runtime\n')
            roles = {'core': ['reranker', 'door'], 'embedder': ['tei-' + str(card)],
                     'ingestion': ['converter', 'builder', 'qdrant'],
                     'full': ['full', 'model_bridge']}[kind]
            for role in roles:
                archive_role = 'full' if role == 'model_bridge' else role
                archive, image_id = classic(archive_role)
                path = 'images/' + archive_role + '.tar'
                if path not in raw:
                    add(path, 'image', archive)
                source = (pins['tei']['variants'][card]['digest'] if role.startswith('tei-') else
                          'sha256:' + 'b' * 64 if role == 'qdrant' else None)
                row = {'role': role, 'path': path, 'image_id': image_id,
                       'config_digest': image_id, 'index_digest': source,
                       'manifest_digest': None, 'archive_format': 'classic', 'source_digest': source}
                b['images'].append(row)
                index.append(dict(row, sha256=sha(archive)))
                if not role.startswith('tei-'):
                    pins['runtime'][role + '_image_id'] = image_id
            for role in sorted(cap.MODEL_ROLES[kind]):
                revision = sha(role.encode())[:40]
                root = 'models/' + role + '/' + revision
                paths = []
                pinned = []
                for basename in ['config.json', 'model.safetensors', 'LICENSE', 'model_code.py']:
                    content = ('synthetic ' + role + ' ' + basename + '\n').encode('ascii')
                    path = root + '/' + basename
                    add(path, 'model', content)
                    paths.append(path)
                    pinned.append({'path': basename, 'sha256': sha(content)})
                model = {'role': role, 'repository': 'synthetic/' + role,
                         'revision': revision, 'root': root, 'files': paths, 'licence_path': root + '/LICENSE'}
                b['models'].append(model)
                pins['models'][role] = {'id': model['repository'], 'revision': revision, 'files': pinned}
        for backend in ['docker-engine', 'docker-desktop']:
            domains = []
            for name in ['download', 'data', 'docker'] + (['desktop-backing'] if backend == 'docker-desktop' else []):
                row = {'domain': name}
                for key in ['download_bytes', 'retained_bytes', 'image_bytes', 'transient_bytes',
                            'rollback_bytes', 'workspace_bytes', 'headroom_bytes', 'steady_bytes', 'peak_bytes']:
                    row[key] = 0
                row['headroom_bytes'] = 20
                if name == 'download':
                    row['download_bytes'] = sum(m['size'] for m in b['members'])
                if name == 'data':
                    row['retained_bytes'] = sum(m['size'] for m in b['members'] if m['category'] != 'image')
                if name in {'docker', 'desktop-backing'}:
                    row['image_bytes'] = sum(m['size'] for m in b['members'] if m['category'] == 'image')
                    row['transient_bytes'] = 4096
                    row['rollback_bytes'] = row['image_bytes']
                row['steady_bytes'] = sum(row[k] for k in ['retained_bytes', 'image_bytes', 'rollback_bytes', 'workspace_bytes'])
                row['peak_bytes'] = row['steady_bytes'] + row['download_bytes'] + row['transient_bytes'] + row['headroom_bytes']
                domains.append(row)
            b['storage_budgets'].append({'backend': backend, 'evidence': 'evidence/storage.json', 'domains': domains})
        b['members'].sort(key=lambda r: r['path'])
        manifest['bundles'].append(b)
        return b
    runtime = [('core', 'core', None)]
    runtime.extend(('embedder-' + card, 'embedder', card) for card in sorted(cap.GPU_CLASSES))
    runtime.append(('ingestion', 'ingestion', None))
    if include_full:
        runtime.append(('full', 'full', None))
    for name, kind, card in runtime:
        bundle(name, kind, card)
        bundle('source-build-' + name, 'corresponding-source-build', target=name)
    manifest['pins_sha256'] = sha(cv.canonical(pins))
    manifest['image_index_sha256'] = sha(cv.canonical(index))
    return manifest, pins, index, payloads


class Capabilities(unittest.TestCase):
    def setUp(self):
        self.manifest, self.pins, self.index, self.payloads = fixture()
        self.temp = tempfile.TemporaryDirectory(dir=SCRATCH, prefix='capability-')
        self.directory = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def changed(self, mutate):
        value = copy.deepcopy(self.manifest)
        mutate(value)
        with self.assertRaises(ValueError):
            cap.validate_manifest(value)

    def crosscheck(self, pins=None, index=None):
        return cap.validate_against_inputs(self.manifest, pins or self.pins, index or self.index,
            pins_sha256=self.manifest['pins_sha256'], image_index_sha256=self.manifest['image_index_sha256'])

    def materialise(self, bundle_id='core'):
        root = self.directory / bundle_id
        root.mkdir()
        for name, raw in self.payloads[bundle_id].items():
            path = root / name
            path.parent.mkdir(exist_ok=True, parents=True)
            path.write_bytes(raw)
        return root

    def add_member(self, bundle, path, category, size, digest):
        row = dict(path=path, category=category, size=size, sha256=digest)
        bundle['members'].append(row)
        for budget in bundle['storage_budgets']:
            domains = {domain['domain']: domain for domain in budget['domains']}
            domains['download']['download_bytes'] += size
            domains['download']['peak_bytes'] += size
            if category != 'image':
                domains['data']['retained_bytes'] += size
                domains['data']['steady_bytes'] += size
                domains['data']['peak_bytes'] += size
        return row

    def test_complete_optional_graph_and_alias(self):
        self.assertIs(cap.validate_manifest(self.manifest), self.manifest)
        self.crosscheck()
        full = next(b for b in self.manifest['bundles'] if b['id'] == 'full')
        self.assertEqual(len({r['path'] for r in full['images']}), 1)

    def test_optional_full_absent(self):
        cap.validate_manifest(fixture(False)[0])

    def test_seven_classes_shared_core_and_tei_only_addons(self):
        self.assertEqual(set(self.manifest['hardware_classes']), {
            'turing', 'ampere80', 'ampere86', 'ada', 'hopper', 'blackwell100', 'blackwell120'})
        bundles = {b['id']: b for b in self.manifest['bundles']}
        self.assertEqual({r['role'] for r in bundles['core']['images']}, {'reranker', 'door'})
        self.assertIsNone(bundles['core']['hardware_class'])
        for card in self.manifest['hardware_classes']:
            with self.subTest(card=card):
                addon = bundles['embedder-' + card]
                self.assertEqual([r['role'] for r in addon['images']], ['tei-' + card])
                self.assertEqual(addon['models'], [])
                self.assertEqual(addon['requires'], ['core'])
                self.assertEqual({m['category'] for m in addon['members']}, {'image', 'licence', 'provenance'})

    def test_exact_core_and_class_selection_with_optional_closure(self):
        for card in sorted(cap.GPU_CLASSES):
            expected = ['core', 'embedder-' + card]
            self.assertEqual([b['id'] for b in cap.select_runtime_bundles(self.manifest, card)], expected)
            self.assertEqual([b['id'] for b in cap.select_runtime_bundles(self.manifest, card, True)], expected + ['ingestion'])
            self.assertEqual([b['id'] for b in cap.select_runtime_bundles(self.manifest, card, include_full=True)], expected + ['ingestion', 'full'])
        for card in ['cpu', 'tei-cpu', 'unknown', None]:
            with self.subTest(card=card), self.assertRaisesRegex(ValueError, 'exact supported class'):
                cap.select_runtime_bundles(self.manifest, card)
        with self.assertRaisesRegex(ValueError, 'not supplied'):
            cap.select_runtime_bundles(fixture(False)[0], 'turing', include_full=True)
        with self.assertRaisesRegex(ValueError, 'boolean'):
            cap.select_runtime_bundles(self.manifest, 'ada', include_ingestion=1)

    def test_required_class_addon_and_tei_payload_only(self):
        self.changed(lambda v:v.update(bundles=[b for b in v['bundles'] if b['id'] != 'embedder-turing']))
        self.changed(lambda v:next(b for b in v['bundles'] if b['id']=='embedder-turing')['members'].append(
            dict(v['bundles'][0]['members'][0], path='kit/extra.py', category='runtime')))
        self.changed(lambda v:next(b for b in v['bundles'] if b['id']=='embedder-turing')['images'][0].update(role='tei-ada'))

    def test_sealed_licensing_source_copies_in_runtime_bundles(self):
        media = {
            'mineru-4.0.7-py3-none-any.whl': (3645284, 'a08b24f733f0160eb9a836b0bb721640caef61d8d4f0ba2fd3aff28c6718111d'),
            'pymupdf-1.28.2.tar.gz': (87903557, '5e0be7908a715aa20333caddd73f1d6f01e4cd0c26e869fa2dd0b7f344da2249'),
        }
        for bundle in self.manifest['bundles']:
            if bundle['kind'] in {'core', 'ingestion', 'embedder'}:
                dirs = {'licences/source'} if bundle['kind'] == 'core' else {'licenses/source'}
                if bundle['kind'] == 'ingestion':
                    dirs.add('deep32/payload/licences/source')
                for directory in sorted(dirs):
                    for filename, (size, digest) in media.items():
                        self.add_member(bundle, directory + '/' + filename, 'licence', size, digest)
        cap.validate_manifest(self.manifest)

    def test_licensing_exception_refuses_bad_content_path_category_and_size(self):
        path = 'licenses/source/mineru-4.0.7-py3-none-any.whl'
        size = 3645284
        digest = 'a08b24f733f0160eb9a836b0bb721640caef61d8d4f0ba2fd3aff28c6718111d'
        cases = [
            (path, 'licence', size, 'f'*64),
            (path, 'licence', 256 * 1024**2, digest),
            (path, 'wheel', size, digest),
            (path, 'provenance', size, digest),
            ('licenses/mineru-4.0.7-py3-none-any.whl', 'licence', size, digest),
            ('licenses/source/other.whl', 'licence', size, digest),
            ('evidence/large.log', 'provenance', 1024**2, 'e'*64),
        ]
        for member in cases:
            value = copy.deepcopy(self.manifest)
            bundle = next(b for b in value['bundles'] if b['id'] == 'embedder-turing')
            self.add_member(bundle, *member)
            with self.subTest(member=member), self.assertRaisesRegex(ValueError, 'embedder add-on may contain'):
                cap.validate_manifest(value)
        core = self.manifest['bundles'][0]
        self.add_member(core, path, 'wheel', size, digest)
        with self.assertRaisesRegex(ValueError, 'build-only input in runtime'):
            cap.validate_manifest(self.manifest)

    def test_exception_cannot_hide_a_duplicate_at_an_unapproved_path(self):
        core, ingestion = [next(b for b in self.manifest['bundles'] if b['id'] == name)
                           for name in ('core', 'ingestion')]
        size = 87903557
        digest = '5e0be7908a715aa20333caddd73f1d6f01e4cd0c26e869fa2dd0b7f344da2249'
        self.add_member(core, 'licences/source/pymupdf-1.28.2.tar.gz', 'licence', size, digest)
        self.add_member(ingestion, 'licenses/source/pymupdf-1.28.2.tar.gz', 'licence', size, digest)
        self.add_member(ingestion, 'kit/renamed.tar.gz', 'runtime', size, digest)
        with self.assertRaisesRegex(ValueError, 'payload member duplicated'):
            cap.validate_manifest(self.manifest)

    def test_sealed_model_dotfiles_and_shared_tokenizers_still_match_pins(self):
        core, ingestion = [next(b for b in self.manifest['bundles'] if b['id'] == name)
                           for name in ('core', 'ingestion')]
        snapshots = [
            (core, 'tei', 'models/tei/models--jinaai--jina-embeddings-v5-text-small-retrieval/'
                          'snapshots/6856e76bb72982e58de0620458a4e8b3614da340',
             2666, '6eb0f7fc84e94386a13852d46a3e902dad2aeced4ca537a282b2259bc56bfe66'),
            (core, 'reranker', 'models/reranker/models--jinaai--jina-reranker-v3.5/'
                               'snapshots/e8a93f33f0b22108f8c2364f8484ce3422552fbc',
             1570, '34448b82c17d60fec9b65b1f093c115ddbaadc04beb1b0140b6bfed2e012a930'),
            (ingestion, 'mineru_vlm', 'deep32/payload/models/mineru-home/models/MinerU2.5-Pro-2605-1.2B',
             1570, '34448b82c17d60fec9b65b1f093c115ddbaadc04beb1b0140b6bfed2e012a930'),
            (ingestion, 'mineru_small', 'models/mineru-home/models/MinerU-4_models_torch',
             1519, '11ad7efa24975ee4b0c3c3a38ed18737f0658a5f75a0a96787b576a78a023361'),
        ]
        for bundle, role, root, size, digest in snapshots:
            model = next(row for row in bundle['models'] if row['role'] == role)
            previous_root = model['root']
            for row in bundle['members']:
                if row['path'].startswith(previous_root + '/'):
                    row['path'] = root + row['path'][len(previous_root):]
            model['root'] = root
            model['licence_path'] = root + '/LICENSE'
            model['files'] = [root + path[len(previous_root):] for path in model['files']]
            extras = [('.gitattributes', size, digest)]
            if role != 'mineru_small':
                extras.extend([
                    ('merges.txt', 1671853, '8831e4f1a044471340f7c0a83d7bd71306a5b867e95fd870f74d0c5308a904d5'),
                    ('vocab.json', 2776833, 'ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910'),
                ])
            for path, byte_count, checksum in extras:
                member = self.add_member(bundle, root + '/' + path, 'model', byte_count, checksum)
                model['files'].append(member['path'])
                self.pins['models'][role]['files'].append({'path': path, 'sha256': checksum})
            markers = {
                'mineru_vlm': ['.mineru_complete'],
                'mineru_small': ['Layout/PP-DocLayoutV2/.mineru_complete', 'OCR/paddleocr/.mineru_complete'],
            }
            for path in markers.get(role, []):
                self.add_member(bundle, root + '/' + path, 'provenance', 0, sha(b''))
        self.manifest['pins_sha256'] = sha(cv.canonical(self.pins))
        self.crosscheck()

        marker = next(row for row in ingestion['members'] if row['path'].endswith('/.mineru_complete'))
        marker['sha256'] = 'f'*64
        with self.assertRaisesRegex(ValueError, 'unsafe capability'):
            cap.validate_manifest(self.manifest)
        marker['sha256'] = sha(b'')
        budget = ingestion['storage_budgets'][0]
        evidence = budget['evidence']
        budget['evidence'] = marker['path']
        with self.assertRaisesRegex(ValueError, 'storage budget measurement evidence missing'):
            cap.validate_manifest(self.manifest)
        budget['evidence'] = evidence

        model = next(row for row in ingestion['models'] if row['role'] == 'mineru_vlm')
        original = list(model['files'])
        model['files'].remove(model['root'] + '/.gitattributes')
        with self.assertRaisesRegex(ValueError, 'model snapshot differs'):
            cap.validate_manifest(self.manifest)
        model['files'] = original

        wrong_file = next(row for row in ingestion['members'] if row['path'].endswith('/vocab.json'))
        wrong_file['path'] += '.copy'
        with self.assertRaisesRegex(ValueError, 'payload member duplicated'):
            cap.validate_manifest(self.manifest)

    def test_hidden_path_exception_is_exact_and_model_only(self):
        root = 'models/mineru-home/models/MinerU-4_models_torch'
        cap.safe_member(root + '/.gitattributes', model=True)
        cap.safe_member(root + '/Layout/PP-DocLayoutV2/.mineru_complete', completion_marker=True)
        for path, flags in [('/Layout/Other/.mineru_complete', {'completion_marker': True}),
                            ('/Layout/PP-DocLayoutV2/.mineru_complete', {'model': True}),
                            ('/.git/config', {'model': True}),
                            ('/../.gitattributes', {'model': True}),
                            ('/.Gitattributes', {'model': True}),
                            ('/.gitattributes', {})]:
            with self.subTest(path=path, flags=flags), self.assertRaisesRegex(ValueError, 'unsafe capability'):
                cap.safe_member(root + path, **flags)

    def test_cross_bundle_duplicate_payload_and_conflicting_paths(self):
        for category in ['image', 'model']:
            value = copy.deepcopy(self.manifest)
            original = next(m for m in value['bundles'][0]['members'] if m['category']==category)
            target = next(b for b in value['bundles'] if b['id']==('embedder-ada' if category=='image' else 'ingestion'))
            member = next(m for m in target['members'] if m['category']==category)
            member.update(size=original['size'], sha256=original['sha256'])
            with self.subTest(category=category), self.assertRaisesRegex(ValueError, 'payload member duplicated'):
                cap.validate_manifest(value)
        for category in ['runtime', 'source']:
            value = copy.deepcopy(self.manifest)
            member = dict(path='duplicate/one', category=category, size=cap.LARGE_MEMBER_BYTES, sha256='f'*64)
            value['bundles'][0]['members'].append(member)
            value['bundles'][1]['members'].append(dict(member, path='renamed/two'))
            for budget in value['bundles'][0]['storage_budgets']:
                for domain in budget['domains']:
                    if domain['domain']=='download':
                        domain['download_bytes'] += member['size']
                        domain['peak_bytes'] += member['size']
                    elif domain['domain']=='data':
                        for field in ['retained_bytes', 'steady_bytes', 'peak_bytes']:
                            domain[field] += member['size']
            with self.subTest(category=category), self.assertRaisesRegex(ValueError, 'payload member duplicated'):
                cap.validate_manifest(value)
        value = copy.deepcopy(self.manifest)
        member = next(m for m in value['bundles'][1]['members'] if m['path']=='licences/test.txt')
        member['sha256']='f'*64
        with self.assertRaisesRegex(ValueError, 'cross-bundle identity'):
            cap.validate_manifest(value)

    def test_deterministic_fixture_bytes_and_bound_input_hashes(self):
        again, pins, index, _ = fixture()
        self.assertEqual(cv.canonical(again), cv.canonical(self.manifest))
        self.assertEqual(again['pins_sha256'], sha(cv.canonical(pins)))
        self.assertEqual(again['image_index_sha256'], sha(cv.canonical(index)))

    def test_public_fixture_matches_exact_resolved_input_bytes(self):
        root = TOOLS / 'contracts/fixtures/capabilities'
        manifest = cv.strict_load(root / 'manifest.json')
        pins_raw = (root / 'resolved-pins.json').read_bytes()
        index_raw = (root / 'image-index.json').read_bytes()
        cap.validate_against_inputs(manifest, cv.strict_loads(pins_raw), cv.strict_loads(index_raw),
            pins_sha256=sha(pins_raw), image_index_sha256=sha(index_raw))

    def test_isolated_contract_cli_valid_and_semantic_mutation(self):
        path = self.directory / 'manifest.json'
        path.write_bytes(cv.canonical(self.manifest))
        args = [sys.executable, '-I', '-B', str(TOOLS/'contract_validate.py'), 'capability-manifest', str(path)]
        good = subprocess.run(args, cwd=self.directory, capture_output=True, text=True, timeout=20)
        self.assertEqual(good.returncode, 0, good.stderr)
        value = copy.deepcopy(self.manifest)
        value['bundles'][0]['images'][0]['role'] = 'converter'
        path.write_bytes(cv.canonical(value))
        bad = subprocess.run(args, cwd=self.directory, capture_output=True, text=True, timeout=20)
        self.assertEqual(bad.returncode, 2, bad.stderr)
        self.assertNotIn('Traceback', bad.stderr)

    def test_trusted_hash_precedes_parse_and_tamper_refuses(self):
        path = self.directory / 'CAPABILITIES.json'
        raw = cv.canonical(self.manifest)
        path.write_bytes(raw)
        self.assertEqual(cap.read_authenticated(path, sha(raw)), self.manifest)
        path.write_bytes(raw.replace(b'alpha.3', b'alpha.1'))
        with self.assertRaisesRegex(ValueError, 'authenticated'):
            cap.read_authenticated(path, sha(raw))

    def test_missing_authentication_and_requested_release_refuse(self):
        path = self.directory / 'CAPABILITIES.json'
        raw = cv.canonical(self.manifest)
        path.write_bytes(raw)
        for trusted in [None, '', 'f' * 64]:
            with self.subTest(trusted=trusted), self.assertRaises(ValueError):
                cap.read_authenticated(path, trusted)
        with self.assertRaisesRegex(ValueError, 'requested release'):
            cap.read_authenticated(path, sha(raw), expected_release='0.1.0-alpha.1')

    def test_legacy_versions_never_fall_back(self):
        with self.assertRaisesRegex(ValueError, 'version 2 shared-core'):
            cap.validate_manifest(dict(self.manifest, format_version=1))
        self.changed(lambda v: v.update(format_version=0))
        self.changed(lambda v: v.update(release='0.1.0-alpha.1'))
        self.changed(lambda v: v.update(format='deep32-forge-pins/1'))

    def test_unknown_fields_at_every_level(self):
        targets = [lambda v:v, lambda v:v['bundles'][0], lambda v:v['bundles'][0]['members'][0],
                   lambda v:v['bundles'][0]['images'][0], lambda v:v['bundles'][0]['models'][0],
                   lambda v:v['bundles'][0]['storage_budgets'][0]['domains'][0]]
        for target in targets:
            with self.subTest(target=target):
                self.changed(lambda v:target(v).update(unknown=True))

    def test_duplicate_keys_and_nonfinite_json(self):
        for raw in [b'{"format":1,"format":2}', b'{"x":NaN}']:
            path = self.directory / 'bad.json'
            path.write_bytes(raw)
            with self.assertRaises(ValueError):
                cap.read_authenticated(path, sha(raw))

    def test_duplicate_extra_missing_and_wrong_image_roles(self):
        for roles in [['door', 'reranker', 'reranker'], ['door', 'converter', 'tei-ada'],
                      ['door', 'reranker'], ['door', 'reranker', 'tei-hopper']]:
            def mutate(v):
                rows = v['bundles'][0]['images']
                v['bundles'][0]['images'] = [dict(rows[0], role=r) for r in roles]
            self.changed(mutate)

    def test_gpu_class_exact_no_cpu_or_fallback(self):
        self.changed(lambda v:v.update(hardware_classes=['cpu']))
        self.changed(lambda v:v.update(hardware_classes=['ada', 'hopper']))
        self.changed(lambda v:v['bundles'][0].update(hardware_class='hopper'))
        pins = copy.deepcopy(self.pins)
        pins['tei']['variants']['ada']['experimental'] = True
        self.crosscheck(pins=pins)
        pins['tei']['variants'].pop('turing')
        with self.assertRaisesRegex(ValueError, 'classes'):
            self.crosscheck(pins=pins)

    def test_single_ingestion_required(self):
        self.changed(lambda v:v['bundles'].extend([copy.deepcopy(v['bundles'][2])]))
        self.changed(lambda v:v.update(bundles=[b for b in v['bundles'] if b['kind'] != 'ingestion']))

    def test_source_build_companion_closure_and_graph(self):
        self.changed(lambda v:v.update(bundles=v['bundles'][:-1]))
        self.changed(lambda v:v['bundles'][1].update(corresponds_to='absent'))
        self.changed(lambda v:v['bundles'][2].update(requires=['full']))
        self.changed(lambda v:v['bundles'][1]['members'].__setitem__(0, dict(v['bundles'][1]['members'][0], category='runtime')))

    def test_paths_traversal_absolute_windows_reserved_and_case_collisions(self):
        for path in ['../escape', '/absolute', 'C:/absolute', 'a\\b', 'models//x', 'nul.txt', 'data/x.', 'a/./b']:
            self.changed(lambda v:v['bundles'][0]['members'][0].update(path=path))
        self.changed(lambda v:v['bundles'][0]['members'].append(dict(v['bundles'][0]['members'][0], path=v['bundles'][0]['members'][0]['path'].upper())))

    def test_file_directory_collision_and_build_only_runtime_refuse(self):
        self.changed(lambda v:v['bundles'][0]['members'].append(dict(v['bundles'][0]['members'][0],path='models')))
        self.changed(lambda v:v['bundles'][0]['members'][0].update(category='wheel'))

    def test_complete_snapshot_and_licences_required(self):
        self.changed(lambda v:v['bundles'][0]['models'][0]['files'].pop())
        self.changed(lambda v:v['bundles'][0]['models'][0].update(licence_path='absent'))
        self.changed(lambda v:v['bundles'][0]['members'].__setitem__(0,dict(v['bundles'][0]['members'][0],category='runtime')))

    def test_model_revision_repository_and_complete_pin_identity(self):
        for field,value in [('revision','e'*40),('id','synthetic/other')]:
            pins = copy.deepcopy(self.pins)
            pins['models']['tei'][field] = value
            with self.assertRaisesRegex(ValueError,'model'):
                self.crosscheck(pins=pins)
        pins = copy.deepcopy(self.pins)
        pins['models']['tei']['files'].pop()
        with self.assertRaisesRegex(ValueError,'complete'):
            self.crosscheck(pins=pins)

    def test_resolved_pin_and_index_hash_mismatch(self):
        with self.assertRaisesRegex(ValueError,'hash'):
            cap.validate_against_inputs(self.manifest,self.pins,self.index,pins_sha256='f'*64,image_index_sha256='f'*64)

    def test_image_pin_index_and_shared_alias_mutations(self):
        pins = copy.deepcopy(self.pins)
        pins['runtime']['reranker_image_id'] = 'sha256:'+'e'*64
        with self.assertRaisesRegex(ValueError,'image digest'):
            self.crosscheck(pins=pins)
        index = copy.deepcopy(self.index)
        index[0]['config_digest']='sha256:'+'f'*64
        with self.assertRaisesRegex(ValueError,'binding'):
            self.crosscheck(index=index)
        self.changed(lambda v:next(b for b in v['bundles'] if b['id']=='full')['images'][1].update(config_digest='sha256:'+'f'*64,image_id='sha256:'+'f'*64))

    def test_duplicate_full_archive_must_be_an_explicit_alias(self):
        def mutate(v):
            full = next(b for b in v['bundles'] if b['id']=='full')
            full['images'][1]['path'] = 'images/bridge.tar'
            member = next(m for m in full['members'] if m['path'] == 'images/full.tar')
            full['members'].append(dict(member, path='images/bridge.tar'))
        self.changed(mutate)

    def test_missing_model_input_and_extra_catalogue_role_refuse(self):
        pins = copy.deepcopy(self.pins)
        del pins['models']['tei']['files']
        with self.assertRaisesRegex(ValueError, 'missing upstream'):
            self.crosscheck(pins=pins)
        with self.assertRaisesRegex(ValueError, 'unrequested roles'):
            self.crosscheck(index=self.index + [dict(self.index[0], role='tei-cpu')])

    def test_staged_cpu_embedder_may_stand_unselected_in_the_index(self):
        # The real producer stages tei-cpu when the pins carry the experimental cpu variant.
        pins = copy.deepcopy(self.pins)
        pins['tei']['variants']['cpu'] = {'experimental': True, 'digest': 'sha256:' + sha(b'source-cpu')}
        cpu_row = dict(self.index[0], role='tei-cpu', path='images/tei-cpu.tar')
        self.crosscheck(pins=pins, index=self.index + [cpu_row])
        with self.assertRaisesRegex(ValueError, 'unrequested roles'):
            self.crosscheck(pins=pins, index=self.index + [cpu_row, dict(cpu_row, role='tei-extra')])
        with self.assertRaisesRegex(ValueError, 'unrequested roles'):
            self.crosscheck(pins=pins, index=self.index[1:] + [cpu_row])

    def test_measured_budget_evidence_domains_and_peak(self):
        self.changed(lambda v:v['bundles'][0]['storage_budgets'][0].update(evidence='absent'))
        self.changed(lambda v:v['bundles'][0]['storage_budgets'][0]['domains'].pop())
        self.changed(lambda v:v['bundles'][0]['storage_budgets'][0]['domains'][0].update(peak_bytes=0))
        self.changed(lambda v:v['bundles'][0]['storage_budgets'][0]['domains'][0].update(download_bytes=0))
        self.changed(lambda v:v['bundles'][0]['storage_budgets'][0]['domains'][0].update(download_bytes=True))
        self.changed(lambda v:v['bundles'][0]['storage_budgets'][0]['domains'][1].update(retained_bytes=0,steady_bytes=0))

    def test_exact_files_and_existing_archive_verifier(self):
        for name in self.payloads:
            root = self.materialise(name)
            expected = len(self.payloads[name])
            self.assertEqual(cap.verify_bundle_files(self.manifest,name,root),expected)

    def test_missing_extra_and_mutated_files(self):
        root = self.materialise()
        path = root/'kit/runtime.py'
        original = path.read_bytes()
        for action in ['missing','mutated','extra']:
            if action == 'missing': path.unlink()
            elif action == 'mutated': path.write_bytes(b'mutated')
            else:
                path.write_bytes(original)
                (root/'unexpected.txt').write_bytes(b'extra')
            with self.subTest(action=action),self.assertRaises(ValueError):
                cap.verify_bundle_files(self.manifest,'core',root)

    def test_wrong_capability_and_corrupt_archive_binding(self):
        root=self.materialise()
        with self.assertRaisesRegex(ValueError,'wrong or absent'):
            cap.verify_bundle_files(self.manifest,'absent',root)
        archive=root/'images/door.tar'
        archive.write_bytes(b'not a tar')
        b=self.manifest['bundles'][0]
        m=next(m for m in b['members'] if m['path']=='images/door.tar')
        m.update(size=archive.stat().st_size,sha256=sha(archive.read_bytes()))
        with self.assertRaises((ValueError,tarfile.TarError)):
            cap.verify_bundle_files(self.manifest,'core',root)

    def test_unsafe_bundle_root_refused_before_traversal(self):
        from unittest.mock import patch
        info = os.stat(self.directory)
        class FakeInfo:
            st_mode = info.st_mode
            st_file_attributes = 0x400
        original = Path.lstat
        def lstat(path, *args, **kwargs):
            return FakeInfo() if path == self.directory else original(path, *args, **kwargs)
        with patch.object(Path, 'lstat', lstat), patch.object(Path, 'rglob', side_effect=AssertionError('traversed unsafe root')):
            with self.assertRaisesRegex(ValueError,'ordinary directories'):
                cap.verify_bundle_files(self.manifest,'core',self.directory)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--scratch-root',type=Path,required=True)
    args,remaining=parser.parse_known_args()
    args.scratch_root.mkdir(parents=True,exist_ok=True)
    if any(args.scratch_root.iterdir()):
        parser.error('scratch root must be empty and task-owned')
    SCRATCH=args.scratch_root.resolve()
    unittest.main(argv=[sys.argv[0],*remaining])
