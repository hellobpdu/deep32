"""Synthetic Docker-save fixtures with task-generated image-store shapes.

The synthetic shape input contains index.json, manifest.json and names, without
content-addressed blobs themselves. Reusing its real digest names for invented
content would defeat descriptor verification. These fixtures preserve one root
index, four Python layers or fourteen Qdrant layers with repeated empty layers,
and sparse foreign/attestation entries, while regenerating every digest and size.
"""
import copy
import gzip
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'build'))
import image_binding as binding

INDEX = 'application/vnd.oci.image.index.v1+json'
MANIFEST = 'application/vnd.oci.image.manifest.v1+json'
CONFIG = 'application/vnd.oci.image.config.v1+json'
LAYER = 'application/vnd.oci.image.layer.v1.tar'
EVIDENCE = json.loads((Path(__file__).with_name('data') / 'synthetic-image-store-evidence.json').read_text(encoding='ascii'))


def data(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii')


def digest(value):
    return 'sha256:' + hashlib.sha256(value).hexdigest()


def layer_bytes(number):
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode='w') as archive:
        content = ('synthetic layer ' + str(number) + '\n').encode('ascii')
        member = tarfile.TarInfo('file-' + str(number))
        member.size = len(content)
        archive.addfile(member, io.BytesIO(content))
    return result.getvalue()


class Fixture:
    def __init__(self, classic=False, single=False, shape='python', compressed=False):
        self.files = {}
        self.extra_members = []
        # Qdrant's evidence has fourteen positions, including the same empty
        # layer seven times; repeated layer descriptors are valid and ordered.
        evidence = EVIDENCE['python' if shape == 'python' else 'qdrant_qdrant']
        source_layers = evidence['manifest'][0]['Layers']
        positions = {}
        numbers = [positions.setdefault(name, len(positions)) for name in source_layers]
        raw_layers = [layer_bytes(number) for number in numbers]
        layers = [gzip.compress(value, mtime=0) if compressed else value for value in raw_layers]
        self.config = {'os': 'linux', 'architecture': 'amd64',
                       'rootfs': {'type': 'layers', 'diff_ids': [digest(value) for value in raw_layers]}}
        self.config_desc = self.blob(data(self.config), CONFIG)
        self.layers = [self.blob(value, LAYER + ('+gzip' if compressed else '')) for value in layers]
        self.manifest = {'schemaVersion': 2, 'mediaType': MANIFEST,
                         'config': self.config_desc, 'layers': self.layers}
        self.leaf = self.blob(data(self.manifest), MANIFEST)
        self.leaf['platform'] = {'os': 'linux', 'architecture': 'amd64'}
        self.records = copy.deepcopy(evidence['manifest'])
        self.records[0].update(Config=self.name(self.config_desc),
                               Layers=[self.name(row) for row in self.layers])
        self.root = copy.deepcopy(self.leaf)
        if not single:
            missing_arm = {'mediaType': MANIFEST, 'digest': 'sha256:' + '1' * 64,
                           'size': 123, 'platform': {'os': 'linux', 'architecture': 'arm64'}}
            missing_attestation = {'mediaType': MANIFEST, 'digest': 'sha256:' + '2' * 64,
                                   'size': 123, 'platform': {'os': 'unknown', 'architecture': 'unknown'}}
            self.root_index = {'schemaVersion': 2, 'mediaType': INDEX,
                               'manifests': [self.leaf, missing_arm, missing_attestation]}
            self.root = self.blob(data(self.root_index), INDEX)
        self.index = copy.deepcopy(evidence['index'])
        self.index['manifests'] = [self.root]
        self.classic = classic
        if classic:
            self.files = {self.config_desc['digest'].split(':')[1] + '.json': data(self.config)}
            self.records[0]['Config'] = next(iter(self.files))
            self.records[0]['Layers'] = []
            for position, value in enumerate(raw_layers):
                name = 'layer-' + str(position) + '/layer.tar'
                self.files[name] = value
                self.records[0]['Layers'].append(name)
        self.pin = self.root['digest']
        self.config_id = self.config_desc['digest']

    @staticmethod
    def name(desc):
        return 'blobs/sha256/' + desc['digest'].split(':')[1]

    def blob(self, content, media_type):
        row = {'mediaType': media_type, 'digest': digest(content), 'size': len(content)}
        self.files[self.name(row)] = content
        return row

    def rebuild(self):
        self.leaf = self.blob(data(self.manifest), MANIFEST)
        self.leaf['platform'] = {'os': 'linux', 'architecture': 'amd64'}
        self.root_index['manifests'][0] = self.leaf
        self.root = self.blob(data(self.root_index), INDEX)
        self.index['manifests'] = [self.root]
        self.pin = self.root['digest']

    def write(self, path):
        files = dict(self.files)
        files['manifest.json'] = data(self.records)
        if not self.classic:
            files['index.json'] = data(self.index)
        with tarfile.open(path, mode='w') as archive:
            for name, content in sorted(files.items()):
                row = tarfile.TarInfo(name)
                row.size = len(content)
                archive.addfile(row, io.BytesIO(content))
            for row, content in self.extra_members:
                archive.addfile(row, io.BytesIO(content) if row.isfile() else None)
        return path


def make_archive(path, form='oci', single=False, role='base'):
    """Write a fixture and return (measured binding, full inspect JSON records)."""
    if form not in ('oci', 'classic') or role not in ('base', 'qdrant'):
        raise ValueError('invalid synthetic archive request')
    fixture = Fixture(classic=form == 'classic', single=single,
                      shape='python' if role == 'base' else 'qdrant')
    fixture.write(path)
    identity = fixture.config_id if form == 'classic' else fixture.pin
    entry = binding.archive_binding(path, source_digest=fixture.pin, inspected_id=identity)
    repository = 'python' if role == 'base' else 'qdrant/qdrant'
    tag = 'python:3.12-slim-trixie' if role == 'base' else 'qdrant/qdrant:v1.19.1'
    inspected = [{'Id': identity, 'Os': 'linux', 'Architecture': 'amd64',
                  'RepoDigests': [repository + '@' + fixture.pin], 'RepoTags': [tag],
                  'Config': {'Labels': {'org.opencontainers.image.version': '1.19.1'}}}]
    return entry, inspected


class ImageBindingTests(unittest.TestCase):
    def test_fixture_shapes_come_from_recorded_evidence(self):
        for shape, key in (('python', 'python'), ('qdrant', 'qdrant_qdrant')):
            evidence = EVIDENCE[key]
            fixture = Fixture(shape=shape)
            self.assertEqual(evidence['inspect']['Id'], evidence['index']['manifests'][0]['digest'])
            # The supplied member lists are prefixes, not complete tar listings.
            self.assertIn('blobs/sha256/' + evidence['inspect']['Id'].split(':')[1], evidence['members'])
            self.assertEqual(len(fixture.layers), len(evidence['manifest'][0]['Layers']))
            self.assertEqual(len({row['digest'] for row in fixture.layers}),
                             len(set(evidence['manifest'][0]['Layers'])))
            self.assertEqual(fixture.records[0]['RepoTags'], evidence['manifest'][0]['RepoTags'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'image.tar'

    def measure(self, fixture, **kwargs):
        return binding.archive_binding(fixture.write(self.path), **kwargs)

    def test_containerd_index_and_config_inspect_forms(self):
        for shape in ('python', 'qdrant'):
            fixture = Fixture(shape=shape)
            for inspected in (fixture.pin, fixture.config_id):
                entry = self.measure(fixture, source_digest=fixture.pin, inspected_id=inspected)
                self.assertEqual(entry, {'image_id': fixture.config_id,
                                         'config_digest': fixture.config_id,
                                         'index_digest': fixture.pin,
                                         'manifest_digest': fixture.leaf['digest'],
                                         'archive_format': 'oci'})
                self.assertEqual(binding.check_archive(self.path, entry, fixture.pin), entry)

    def test_single_platform_manifest_pin(self):
        fixture = Fixture(single=True)
        entry = self.measure(fixture, source_digest=fixture.pin, inspected_id=fixture.pin)
        self.assertEqual(entry['index_digest'], entry['manifest_digest'])

    def test_gzip_layers_bind_uncompressed_diff_ids(self):
        fixture = Fixture(compressed=True)
        self.assertEqual(self.measure(fixture)['config_digest'], fixture.config_id)

    def test_classic_pin_requires_inspect_config_id(self):
        fixture = Fixture(classic=True)
        with self.assertRaisesRegex(ValueError, 'requires measured inspect'):
            self.measure(fixture, source_digest=fixture.pin)
        with self.assertRaisesRegex(ValueError, 'inspect identity'):
            self.measure(fixture, source_digest=fixture.pin, inspected_id=fixture.pin)
        entry = self.measure(fixture, source_digest=fixture.pin, inspected_id=fixture.config_id)
        self.assertEqual(entry['archive_format'], 'classic')
        self.assertIsNone(entry['manifest_digest'])
        self.assertEqual(binding.check_archive(self.path, entry, fixture.pin), entry)

    def test_unpinned_local_classic(self):
        fixture = Fixture(classic=True)
        entry = self.measure(fixture)
        self.assertIsNone(entry['index_digest'])
        self.assertEqual(binding.check_archive(self.path, entry), entry)

    def test_wrong_pinned_index(self):
        with self.assertRaisesRegex(ValueError, 'pinned source'):
            self.measure(Fixture(), source_digest='sha256:' + 'f' * 64)

    def test_missing_amd64_manifest(self):
        fixture = Fixture()
        del fixture.files[fixture.name(fixture.leaf)]
        with self.assertRaisesRegex(ValueError, 'missing'):
            self.measure(fixture)

    def test_no_amd64_manifest(self):
        fixture = Fixture()
        fixture.root_index['manifests'] = fixture.root_index['manifests'][1:]
        fixture.root = fixture.blob(data(fixture.root_index), INDEX)
        fixture.index['manifests'] = [fixture.root]
        with self.assertRaisesRegex(ValueError, 'exactly one linux/amd64'):
            self.measure(fixture)

    def test_sparse_missing_descriptor_requires_other_platform(self):
        fixture = Fixture()
        del fixture.root_index['manifests'][1]['platform']
        fixture.root = fixture.blob(data(fixture.root_index), INDEX)
        fixture.index['manifests'] = [fixture.root]
        with self.assertRaisesRegex(ValueError, 'missing'):
            self.measure(fixture)

    def test_nested_index(self):
        fixture = Fixture()
        wrapper = {'schemaVersion': 2, 'mediaType': INDEX, 'manifests': [fixture.root]}
        fixture.root = fixture.blob(data(wrapper), INDEX)
        fixture.index['manifests'] = [fixture.root]
        entry = self.measure(fixture, source_digest=fixture.root['digest'])
        self.assertEqual(entry['manifest_digest'], fixture.leaf['digest'])

    def test_present_foreign_descriptor_is_measured(self):
        fixture = Fixture()
        foreign = fixture.blob(b'not an image manifest', MANIFEST)
        foreign['platform'] = {'os': 'linux', 'architecture': 'arm64'}
        fixture.root_index['manifests'][1] = foreign
        fixture.rebuild()
        fixture.files[fixture.name(foreign)] += b'corrupt'
        with self.assertRaisesRegex(ValueError, 'digest or size'):
            self.measure(fixture)

    def test_config_mismatch_between_docker_and_oci(self):
        fixture = Fixture()
        alternate = dict(fixture.config, created='synthetic alternate')
        desc = fixture.blob(data(alternate), CONFIG)
        fixture.records[0]['Config'] = fixture.name(desc)
        with self.assertRaisesRegex(ValueError, 'config differs'):
            self.measure(fixture)

    def test_extra_docker_image(self):
        fixture = Fixture()
        fixture.records.append(copy.deepcopy(fixture.records[0]))
        with self.assertRaisesRegex(ValueError, 'exactly one image'):
            self.measure(fixture)

    def test_extra_oci_root(self):
        fixture = Fixture()
        fixture.index['manifests'].append(fixture.root)
        with self.assertRaisesRegex(ValueError, 'exactly one root'):
            self.measure(fixture)

    def test_two_available_amd64_leaves(self):
        fixture = Fixture()
        fixture.root_index['manifests'].append(fixture.leaf)
        fixture.root = fixture.blob(data(fixture.root_index), INDEX)
        fixture.index['manifests'] = [fixture.root]
        with self.assertRaisesRegex(ValueError, 'exactly one linux/amd64'):
            self.measure(fixture)

    def test_config_bytes_do_not_match_name(self):
        fixture = Fixture()
        fixture.files[fixture.name(fixture.config_desc)] += b' '
        with self.assertRaisesRegex(ValueError, 'config name'):
            self.measure(fixture)

    def test_root_descriptor_bytes_and_size_measured(self):
        for change in ('bytes', 'size'):
            fixture = Fixture()
            if change == 'bytes':
                fixture.files[fixture.name(fixture.root)] += b' '
            else:
                fixture.root['size'] += 1
            with self.assertRaisesRegex(ValueError, 'digest or size'):
                self.measure(fixture)

    def test_layer_bytes_and_size_measured(self):
        fixture = Fixture()
        fixture.files[fixture.name(fixture.layers[0])] += b'x'
        with self.assertRaisesRegex(ValueError, 'digest or size'):
            self.measure(fixture)

    def test_layer_order_cross_checked(self):
        fixture = Fixture()
        fixture.records[0]['Layers'].reverse()
        with self.assertRaisesRegex(ValueError, 'layers differ'):
            self.measure(fixture)

    def test_selected_layer_must_exist(self):
        fixture = Fixture()
        del fixture.files[fixture.name(fixture.layers[0])]
        with self.assertRaisesRegex(ValueError, 'missing'):
            self.measure(fixture)

    def test_platform_mismatch(self):
        fixture = Fixture()
        fixture.root_index['manifests'][0]['platform']['architecture'] = 'arm64'
        fixture.root = fixture.blob(data(fixture.root_index), INDEX)
        fixture.index['manifests'] = [fixture.root]
        with self.assertRaisesRegex(ValueError, 'platform differ'):
            self.measure(fixture)

    def test_config_platform_mismatch(self):
        fixture = Fixture(classic=True)
        fixture.config['architecture'] = 'arm64'
        content = data(fixture.config)
        name = digest(content).split(':')[1] + '.json'
        fixture.files[name] = content
        fixture.records[0]['Config'] = name
        with self.assertRaisesRegex(ValueError, 'must be linux/amd64'):
            self.measure(fixture)

    def test_diff_id_mismatch(self):
        fixture = Fixture(classic=True)
        fixture.files[fixture.records[0]['Layers'][0]] = layer_bytes(99)
        with self.assertRaisesRegex(ValueError, 'diff_id differs'):
            self.measure(fixture)

    def test_unsafe_and_duplicate_members(self):
        for name in ('../bad', '/bad', 'bad\\name', './bad', 'x//y', 'manifest.json'):
            fixture = Fixture()
            row = tarfile.TarInfo(name)
            row.size = 0
            fixture.extra_members = [(row, b'')]
            with self.assertRaisesRegex(ValueError, 'unsafe|duplicate'):
                self.measure(fixture)

    def test_symlink_member(self):
        fixture = Fixture()
        row = tarfile.TarInfo('link')
        row.type = tarfile.SYMTYPE
        row.linkname = 'manifest.json'
        fixture.extra_members = [(row, b'')]
        with self.assertRaisesRegex(ValueError, 'unsafe'):
            self.measure(fixture)

    def test_duplicate_json_keys(self):
        fixture = Fixture()
        root = fixture.blob(b'{"schemaVersion":2,"schemaVersion":2}', INDEX)
        fixture.index['manifests'] = [root]
        with self.assertRaisesRegex(ValueError, 'duplicate image metadata key'):
            self.measure(fixture)

    def test_sealed_pair_and_legacy_id_checked(self):
        fixture = Fixture()
        entry = self.measure(fixture)
        for field in ('image_id', 'config_digest', 'index_digest', 'manifest_digest', 'archive_format'):
            altered = dict(entry)
            altered[field] = 'classic' if field == 'archive_format' else 'sha256:' + 'e' * 64
            with self.assertRaises(ValueError):
                binding.check_archive(self.path, altered)

    def test_graph_limit(self):
        fixture = Fixture()
        with patch.object(binding, 'MAX_GRAPH', 1):
            with self.assertRaisesRegex(ValueError, 'graph exceeds'):
                self.measure(fixture)

    def test_depth_limit(self):
        fixture = Fixture()
        with patch.object(binding, 'MAX_DEPTH', 1):
            with self.assertRaisesRegex(ValueError, 'graph exceeds'):
                self.measure(fixture)

    def test_incomplete_binding_record(self):
        fixture = Fixture()
        entry = self.measure(fixture)
        del entry['index_digest']
        with self.assertRaisesRegex(ValueError, 'complete image binding'):
            binding.check_archive(self.path, entry)


class LoadedIdentityTests(unittest.TestCase):
    def setUp(self):
        self.entry = {'image_id': 'sha256:' + 'a' * 64,
                      'config_digest': 'sha256:' + 'a' * 64,
                      'index_digest': 'sha256:' + 'b' * 64,
                      'manifest_digest': 'sha256:' + 'c' * 64,
                      'archive_format': 'oci'}

    def inspected(self, identity=None, os_name='linux', architecture='amd64'):
        value = [{'Id': identity or self.entry['index_digest'],
                  'Os': os_name, 'Architecture': architecture}]
        return subprocess.CompletedProcess([], 0, stdout=json.dumps(value), stderr='')

    def test_index_id_on_containerd(self):
        with patch.object(binding.subprocess, 'run', return_value=self.inspected()) as call:
            self.assertEqual(binding.resolve_loaded(self.entry), self.entry['index_digest'])
        self.assertEqual(call.call_args.args[0], ['docker', 'image', 'inspect', self.entry['index_digest']])

    def test_classic_store_falls_back_to_config(self):
        missing = subprocess.CalledProcessError(1, ['docker', 'image', 'inspect'])
        with patch.object(binding.subprocess, 'run',
                          side_effect=[missing, self.inspected(self.entry['config_digest'])]) as call:
            self.assertEqual(binding.resolve_loaded(self.entry), self.entry['config_digest'])
        self.assertEqual(call.call_args.args[0][-1], self.entry['config_digest'])

    def test_inspect_index_may_return_config(self):
        with patch.object(binding.subprocess, 'run', return_value=self.inspected(self.entry['config_digest'])):
            self.assertEqual(binding.resolve_loaded(self.entry), self.entry['config_digest'])

    def test_wrong_identity_platform_and_multiple_records_refused(self):
        cases = [self.inspected('sha256:' + 'd' * 64),
                 self.inspected(os_name='windows'), self.inspected(architecture='arm64'),
                 subprocess.CompletedProcess([], 0, stdout='[{},{}]', stderr='')]
        for result in cases:
            with patch.object(binding.subprocess, 'run', return_value=result):
                with self.assertRaises(ValueError):
                    binding.resolve_loaded(self.entry)

    def test_missing_pair_refused(self):
        with patch.object(binding.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, [])):
            with self.assertRaisesRegex(ValueError, 'not found'):
                binding.resolve_loaded(self.entry)


if __name__ == '__main__':
    unittest.main()
