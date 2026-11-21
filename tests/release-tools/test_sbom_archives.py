"""Synthetic, offline coverage of image and runtime archive input adapters."""

import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import zlib


sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src' / 'release-tools'))
from sbom import archives


def hashed(data):
    return hashlib.sha256(data).hexdigest()


def encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode('ascii')


def write_tar(path, rows):
    with tarfile.open(path, 'w', format=tarfile.USTAR_FORMAT) as archive:
        for row in rows:
            name, data = row[:2]
            member = tarfile.TarInfo(name)
            member.mode = 0o644
            member.mtime = 0
            member.size = len(data)
            if len(row) > 2:
                member.type = row[2]
                member.linkname = row[3] if len(row) > 3 else ''
                member.size = 0
            archive.addfile(member, io.BytesIO(data) if member.isfile() else None)


def image_fixture(path, zstd=False):
    layer = b'synthetic opaque layer bytes; no filesystem is read'
    config = encoded({'architecture': 'amd64', 'os': 'linux',
                      'rootfs': {'type': 'layers',
                                 'diff_ids': ['sha256:' + hashed(layer)]}})
    config_hash = hashed(config)
    config_name = config_hash + '.json'
    layer_name = 'layer/layer.tar'
    binding = {'image_id': 'sha256:' + config_hash,
               'config_digest': 'sha256:' + config_hash,
               'index_digest': None, 'manifest_digest': None,
               'archive_format': 'classic'}
    rows = []
    if zstd:
        config_name = 'blobs/sha256/' + config_hash
        layer_name = 'blobs/sha256/' + hashed(layer)
        manifest_type = 'application/vnd.oci.image.manifest.v1+json'
        manifest = encoded({'schemaVersion': 2, 'mediaType': manifest_type,
                            'config': {'mediaType': 'application/vnd.oci.image.config.v1+json',
                                       'digest': 'sha256:' + config_hash, 'size': len(config)},
                            'layers': [{'mediaType': 'application/vnd.oci.image.layer.v1.tar+zstd',
                                        'digest': 'sha256:' + hashed(layer), 'size': len(layer)}]})
        manifest_hash = hashed(manifest)
        index = encoded({'schemaVersion': 2,
                         'mediaType': 'application/vnd.oci.image.index.v1+json',
                         'manifests': [{'mediaType': manifest_type,
                                        'digest': 'sha256:' + manifest_hash,
                                        'size': len(manifest),
                                        'platform': {'architecture': 'amd64', 'os': 'linux'}}]})
        rows.extend([('index.json', index), ('blobs/sha256/' + manifest_hash, manifest)])
        binding.update(index_digest='sha256:' + manifest_hash,
                       manifest_digest='sha256:' + manifest_hash, archive_format='oci')
    docker_manifest = encoded([{'Config': config_name, 'RepoTags': ['synthetic:fixture'],
                                'Layers': [layer_name]}])
    rows.extend([('manifest.json', docker_manifest), (config_name, config), (layer_name, layer)])
    write_tar(path, rows)
    return binding


def component(name, usage='runtime'):
    return {'id': name, 'kind': 'package', 'name': name, 'version': '1.0',
            'architecture': 'amd64', 'usage': usage, 'files': [], 'dependencies': []}


def inventory(binding):
    return {'format': archives.INVENTORY_FORMAT,
            'config_digest': binding['config_digest'], 'coverage': 'complete',
            'components': [component('inherited-base'), component('application')]}


class ImageArchives(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='sbom-image-synthetic-')
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'synthetic-image.tar'
        self.binding = image_fixture(self.path)

    def test_complete_inventory_retains_base_and_application(self):
        sealed = inventory(self.binding)
        original = copy.deepcopy(sealed)
        with mock.patch.object(archives.build_image_binding.subprocess, 'run',
                               side_effect=AssertionError('process execution forbidden')):
            actual = archives.normalise_image(self.path, self.binding, sealed)
        self.assertEqual([row['name'] for row in actual['components']],
                         ['application', 'inherited-base'])
        self.assertEqual(actual['binding'], self.binding)
        self.assertEqual(actual['size'], self.path.stat().st_size)
        self.assertEqual(actual['sha256'], hashed(self.path.read_bytes()))
        self.assertNotEqual('sha256:' + actual['sha256'], self.binding['config_digest'])
        self.assertEqual(sealed, original)

    def test_absent_inventory_is_precise_missing_input(self):
        with self.assertRaisesRegex(ValueError, archives.MISSING_INVENTORY):
            archives.normalise_image(self.path, self.binding)

    def test_incomplete_inventory_is_missing_input(self):
        sealed = inventory(self.binding)
        sealed['coverage'] = 'partial'
        with self.assertRaisesRegex(ValueError, archives.MISSING_INVENTORY):
            archives.normalise_image(self.path, self.binding, sealed)

    def test_config_mismatch_is_refused(self):
        sealed = inventory(self.binding)
        sealed['config_digest'] = 'sha256:' + '0' * 64
        with self.assertRaisesRegex(ValueError, 'inventory config digest differs'):
            archives.normalise_image(self.path, self.binding, sealed)

    def test_duplicate_component_identity_is_refused(self):
        sealed = inventory(self.binding)
        sealed['components'].append(copy.deepcopy(sealed['components'][0]))
        with self.assertRaisesRegex(ValueError, 'duplicate.*component identity'):
            archives.normalise_image(self.path, self.binding, sealed)

    def test_invalid_component_type_is_refused(self):
        sealed = inventory(self.binding)
        sealed['components'][0]['version'] = 3
        with self.assertRaisesRegex(ValueError, 'component version'):
            archives.normalise_image(self.path, self.binding, sealed)

    def test_unknown_inventory_field_is_refused(self):
        sealed = inventory(self.binding)
        sealed['invented'] = True
        with self.assertRaisesRegex(ValueError, 'inventory format'):
            archives.normalise_image(self.path, self.binding, sealed)

    def test_binding_tamper_is_refused(self):
        binding = dict(self.binding, config_digest='sha256:' + '1' * 64,
                       image_id='sha256:' + '1' * 64)
        with self.assertRaises(ValueError):
            archives.normalise_image(self.path, binding, inventory(binding))
        with self.assertRaisesRegex(ValueError, 'complete exact image binding record required'):
            archives.normalise_image(self.path, dict(self.binding, invented=True), inventory(self.binding))

    def test_malformed_archive_is_refused(self):
        self.path.write_bytes(b'not a saved image')
        with self.assertRaisesRegex(ValueError, 'invalid saved image archive'):
            archives.normalise_image(self.path, self.binding, inventory(self.binding))

    def test_binder_compression_error_is_a_value_error(self):
        with mock.patch.object(archives.build_image_binding, 'check_archive',
                               side_effect=zlib.error('synthetic corrupt deflate')):
            with self.assertRaisesRegex(ValueError, 'invalid saved image archive'):
                archives.normalise_image(self.path, self.binding, inventory(self.binding))

    def test_zstd_descriptor_requires_sealed_compatible_inventory(self):
        binding = image_fixture(self.path, zstd=True)
        with self.assertRaisesRegex(ValueError, archives.MISSING_INVENTORY):
            archives.normalise_image(self.path, binding)
        # The opaque blob is identity evidence only, never a parsed filesystem.
        actual = archives.normalise_image(self.path, binding, inventory(binding))
        self.assertEqual(actual['coverage'], 'complete')
        self.assertEqual(len(actual['components']), 2)


class RuntimeArchives(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='sbom-runtime-synthetic-')
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / 'synthetic-runtime.tar'
        self.rows = [('deep32/kit/start.sh', b'fixture only\n'),
                     ('deep32/kit/pins.json', b'{"format":"synthetic"}\n')]
        self.members = [{'path': name, 'size': len(data), 'sha256': hashed(data), 'mode': 0o644}
                        for name, data in self.rows]
        write_tar(self.path, self.rows)

    def test_exact_members_are_measured_sorted_and_mode_is_optional(self):
        self.members[0].pop('mode')
        actual = archives.normalise_runtime_archive(self.path, list(reversed(self.members)))
        self.assertEqual([row['path'] for row in actual['members']], sorted(name for name, _ in self.rows))
        self.assertEqual(actual['sha256'], hashed(self.path.read_bytes()))
        self.assertEqual(actual['size'], self.path.stat().st_size)

    def test_member_hash_size_and_mode_tamper_are_refused(self):
        for field, value in [('sha256', '0' * 64), ('size', 45), ('mode', 0o755)]:
            with self.subTest(field=field):
                members = copy.deepcopy(self.members)
                members[0][field] = value
                with self.assertRaisesRegex(ValueError, 'runtime member .* differs'):
                    archives.normalise_runtime_archive(self.path, members)

    def test_extra_and_missing_members_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'unexpected runtime archive member'):
            archives.normalise_runtime_archive(self.path, self.members[:1])
        write_tar(self.path, self.rows[:1])
        with self.assertRaisesRegex(ValueError, 'runtime archive member missing'):
            archives.normalise_runtime_archive(self.path, self.members)

    def test_duplicate_archive_and_manifest_paths_are_refused(self):
        with self.assertRaisesRegex(ValueError, 'duplicate runtime member manifest path'):
            archives.normalise_runtime_archive(self.path, self.members + self.members[:1])
        write_tar(self.path, self.rows + self.rows[:1])
        with self.assertRaisesRegex(ValueError, 'duplicate runtime archive member'):
            archives.normalise_runtime_archive(self.path, self.members)

    def test_traversal_and_absolute_paths_are_refused(self):
        for name in ['../escape', '/absolute', 'a/../escape', './normal',
                     'a//b', 'a\\b', 'C:/escape', 'sealed/file', 'q123/file']:
            with self.subTest(name=name):
                write_tar(self.path, [(name, b'x')])
                with self.assertRaises(ValueError):
                    archives.normalise_runtime_archive(self.path, self.members)

    def test_links_directories_and_special_members_are_refused(self):
        for kind in [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.DIRTYPE, tarfile.FIFOTYPE]:
            with self.subTest(kind=kind):
                write_tar(self.path, [(self.rows[0][0], b'', kind, 'target')])
                with self.assertRaisesRegex(ValueError, 'ordinary regular file'):
                    archives.normalise_runtime_archive(self.path, self.members)

    def test_invalid_manifest_fields_are_refused(self):
        for field, value in [('size', True), ('sha256', 'not-a-hash'), ('mode', True),
                             ('unknown', 'field'), ('path', '../escape')]:
            with self.subTest(field=field):
                members = copy.deepcopy(self.members)
                members[0][field] = value
                with self.assertRaises(ValueError):
                    archives.normalise_runtime_archive(self.path, members)

    def test_malformed_gzip_tar_is_a_value_error(self):
        # A real gzip header followed by DEFLATE's reserved block type.
        # tarfile's automatic compression probing can expose zlib.error here.
        self.path.write_bytes(b'\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff'
                              b'\x07' + b'\x00' * 8)
        with self.assertRaisesRegex(ValueError, 'invalid runtime tar archive'):
            archives.normalise_runtime_archive(self.path, self.members)


if __name__ == '__main__':
    unittest.main()
