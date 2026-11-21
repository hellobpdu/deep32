"""Offline component staging with invented model/source and saved-image inputs."""
import copy
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'build'))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import runtime_stage as runtime
import pack
from provenance import canonical, digest, members
from test_image_binding import make_archive


class RuntimeStageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.stage = self.root / 'acquisition'
        self.kit = self.root / 'kit'
        self.kit.mkdir()
        self.payload = self.stage / 'payload'
        self.payload.mkdir(parents=True)
        self.pins = {'release_profile': 'pack', 'models': {}, 'runtime': {},
                     'tei': {'variants': {}}, 'builder': {}}
        for role in ('tei', 'reranker'):
            model = {'id': 'invented/' + role, 'revision': 'a' * 40}
            base = 'models/' + role + '/models--invented--' + role + '/snapshots/' + model['revision'] + '/'
            for name in ('config.json', 'model.safetensors', 'tokenizer.json', 'remote.py'):
                self.write(base + name, ('invented ' + role + ' ' + name).encode('ascii'))
            model['files'] = [{'path': row['path'], 'sha256': row['sha256']}
                              for row in members(self.payload / base)]
            self.pins['models'][role] = model
        self.write('models/mineru-home/config.yaml', b'invented fixture\n')
        self.write('models/mineru-home/models/invented/weights.bin', b'invented MinerU\n')
        self.write('licences/AGPL-3.0.txt', b'invented licence fixture\n')
        self.write('source/production-converter/source.py', b'# invented source\n')
        self.write('source/production-converter/LICENCE', b'invented source licence\n')
        self.write('evidence/input-attestation.json', canonical({'records': ['evidence/receipt.json']}).encode('ascii'))
        self.write('evidence/receipt.json', b'{}\n')
        self.write('wheelhouse/converter/build.whl', b'invented build dependency\n')
        self.write('repos/ubuntu/package.deb', b'invented acquisition package\n')
        self.images = []
        for role in ('door', 'reranker', 'converter', 'builder', 'qdrant', *('tei-' + value for value in sorted(runtime.CLASSES))):
            path = 'images/' + role + '.tar'
            (self.payload / path).parent.mkdir(exist_ok=True)
            binding, _ = make_archive(self.payload / path, role='qdrant' if role == 'qdrant' else 'base')
            row = {'role': role, 'path': path, 'sha256': digest(self.payload / path), **binding}
            row['source_digest'] = binding['index_digest']
            if role.startswith('tei-'):
                self.pins['tei']['variants'][role[4:]] = {'digest': row['source_digest']}
            else:
                self.pins['runtime'][role + '_image_id'] = row['config_digest']
            if role == 'qdrant':
                row['version'] = '1.19.1'
                self.pins['builder']['qdrant'] = {**binding, 'version': '1.19.1',
                                                'image': 'invented/qdrant@' + row['source_digest']}
            self.images.append(row)
        self.seal()
        # Real archive bindings are exercised above and by stage selection.
        # Full acquisition validation has separate existing tests; this fixture
        # replaces only that gate because its tiny model bytes are invented.
        self.validator = patch.object(runtime, 'validate_source', return_value=1)
        self.validate = self.validator.start()
        self.addCleanup(self.validator.stop)

    def write(self, name, value):
        path = self.payload / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value)

    def seal(self):
        self.write('images/index.json', canonical(self.images).encode('ascii'))
        (self.stage / 'pins.json').write_text(canonical(self.pins), encoding='ascii')
        (self.stage / 'PAYLOAD.json').write_text(canonical(members(self.payload)), encoding='ascii')

    def allow(self, component='core', gpu_class=None, change=None):
        roles = runtime.expected_roles(component, gpu_class)
        prefixes = ('models/tei/', 'models/reranker/') if component == 'core' else ('models/mineru-home/',)
        names = {row['path'] for row in self.images if row['role'] in roles}
        if component != 'embedder':
            names.update(row['path'] for row in members(self.payload)
                         if row['path'].startswith(prefixes + ('licences/', 'evidence/')))
        if component == 'ingestion':
            names.update(row['path'] for row in members(self.payload) if row['path'].startswith('source/'))
        rows = [row for row in members(self.payload) if row['path'] in names]
        if change:
            rows = change(rows)
        path = self.root / ('allow-' + component + ('-' + gpu_class if gpu_class else '') + '.json')
        path.write_text(canonical(rows), encoding='ascii')
        return path

    def build(self, component='core', gpu_class=None, allow=None, out=None):
        return runtime.stage_runtime(self.stage, self.kit, allow or self.allow(component, gpu_class),
                                     component, out or self.root / 'output', 1234567890, gpu_class)

    def refuses(self, component='core', gpu_class=None, allow=None):
        with self.assertRaises((ValueError, KeyError, TypeError)):
            self.build(component, gpu_class, allow)
        self.assertFalse((self.root / 'output').exists())

    def test_core_keeps_complete_models_and_only_two_images(self):
        before = members(self.stage)
        receipt = self.build()
        output = self.root / 'output'
        self.assertEqual(receipt['roles'], ['door', 'reranker'])
        self.assertEqual(members(self.stage), before)
        self.assertEqual(runtime.read_json(output / 'PAYLOAD.json'), members(output / 'payload'))
        names = {row['path'] for row in members(output / 'payload')}
        self.assertFalse(any(name.startswith(('wheelhouse/', 'repos/', 'models/mineru-home/')) for name in names))
        self.assertEqual([row['role'] for row in runtime.read_json(output / 'payload/images/index.json')], ['door', 'reranker'])
        self.validate.assert_called_once_with(self.stage, self.kit)

    def test_each_of_seven_embedder_addons_contains_only_one_archive(self):
        for gpu_class in sorted(runtime.CLASSES):
            with self.subTest(gpu_class=gpu_class):
                output = self.root / ('embedder-' + gpu_class)
                receipt = self.build('embedder', gpu_class, out=output)
                self.assertEqual(receipt['roles'], ['tei-' + gpu_class])
                self.assertEqual({row['path'] for row in members(output / 'payload')},
                                 {'images/tei-' + gpu_class + '.tar', 'images/index.json'})

    def test_ingestion_has_three_roles_mineru_and_corresponding_source(self):
        receipt = self.build('ingestion')
        self.assertEqual(receipt['roles'], ['builder', 'converter', 'qdrant'])
        self.assertTrue((self.root / 'output/payload/source/production-converter/LICENCE').is_file())

    def test_deterministic_bytes_and_epoch(self):
        self.build(out=self.root / 'one')
        self.build(out=self.root / 'two')
        self.assertEqual(members(self.root / 'one'), members(self.root / 'two'))
        self.assertEqual((self.root / 'two/PAYLOAD.json').stat().st_mtime, 1234567890)

    def test_runtime_stage_flows_through_pack_inventory_copy(self):
        self.build()
        # Tiny fake runtime form inputs; no image builder or archiver executes.
        for name in ('LICENSE', 'NOTICE', 'pins.json', 'README.txt', 'build/provenance.py', 'build/thin_forms.py',
                     'forms/load-docker.sh', 'pairing/bin/deep32-pair', 'pairing/bin/pairing-helper',
                     *('pairing/deep32net/' + value + '.py' for value in pack._thin.PAIRING_MODULES)):
            target = self.kit / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('{}\n' if name == 'pins.json' else 'invented fixture\n', encoding='ascii', newline='\n')
        docker, alternate = self.root / 'docker.tar', self.root / 'alternate.tar'
        docker.write_bytes(b'invented thin form')
        alternate.write_bytes(b'invented alternate form')
        media = self.root / 'media'
        pack.prepare(self.root / 'output', self.kit, docker, alternate, media, 1234567890)
        staged = members(self.root / 'output/payload')
        self.assertEqual(staged, members(media / 'deep32/payload'))
        self.assertFalse((media / 'deep32/payload/wheelhouse').exists())
        self.assertFalse((media / 'deep32/payload/images/converter.tar').exists())
        self.assertEqual([row['path'] for row in members(media) if row['path'].endswith('reranker.tar')],
                         ['deep32/payload/images/reranker.tar'])

    def test_full_acquisition_gate_failure_makes_no_output(self):
        self.validate.side_effect = ValueError('invented missing source input')
        self.refuses()

    def test_missing_model_remote_code_refused(self):
        self.refuses(allow=self.allow(change=lambda rows: [row for row in rows if not row['path'].endswith('remote.py')]))

    def test_incomplete_pinned_model_manifest_refused(self):
        self.pins['models']['tei']['files'].pop()
        self.seal()
        self.refuses()

    def test_mixed_model_revision_refused(self):
        self.pins['models']['tei']['revision'] = 'b' * 40
        self.seal()
        self.refuses()

    def test_stale_model_snapshot_may_stay_in_source_but_cannot_ship(self):
        stale = 'models/tei/models--invented--tei/snapshots/' + 'b' * 40 + '/config.json'
        self.write(stale, b'invented old revision')
        self.seal()
        self.refuses()
        allow = self.allow(change=lambda rows: [row for row in rows if row['path'] != stale])
        self.build(allow=allow)
        self.assertFalse((self.root / 'output/payload' / stale).exists())

    def test_mutated_source_refused(self):
        allow = self.allow()
        self.write('evidence/receipt.json', b'changed')
        self.refuses(allow=allow)

    def test_bad_allowlist_hash_size_unknown_fields_or_duplicate_refused(self):
        for field in ('sha256', 'size', 'unknown', 'duplicate', 'case'):
            with self.subTest(field=field):
                def change(rows):
                    if field == 'duplicate':
                        return rows + [copy.deepcopy(rows[-1])]
                    if field == 'case':
                        extra = copy.deepcopy(rows[-1])
                        extra['path'] = extra['path'].upper()
                        return sorted(rows + [extra], key=lambda row: row['path'])
                    rows[0][field] = 'f' * 64 if field == 'sha256' else 0
                    return rows
                self.refuses(allow=self.allow(change=change))

    def test_path_traversal_absolute_drive_backslash_controls_refused(self):
        for value in ('../escape', '/absolute', 'C:/escape', 'a\\b', 'a//b', 'a/./b', 'a\nb',
                      'a./file', 'a /file', 'con.txt', 'a/file?.bin'):
            with self.subTest(value=value):
                def change(rows):
                    rows[0]['path'] = value
                    return rows
                self.refuses(allow=self.allow(change=change))

    def test_duplicate_json_keys_refused(self):
        path = self.allow()
        path.write_text('[{"path":"x","path":"y","size":0,"sha256":"' + 'a' * 64 + '"}]', encoding='ascii')
        self.refuses(allow=path)

    def test_missing_evidence_or_licence_source_refused(self):
        for omitted in ('evidence/receipt.json', 'evidence/input-attestation.json',
                        'licences/AGPL-3.0.txt', 'source/production-converter/LICENCE'):
            with self.subTest(omitted=omitted):
                self.refuses('ingestion', allow=self.allow('ingestion', change=lambda rows: [row for row in rows if row['path'] != omitted]))

    def test_core_cannot_omit_common_licences(self):
        self.refuses(allow=self.allow(change=lambda rows: [row for row in rows if not row['path'].startswith('licences/')]))

    def test_build_only_inputs_and_unselected_models_images_refused(self):
        for extra in ('wheelhouse/converter/build.whl', 'repos/ubuntu/package.deb',
                      'images/tei-turing.tar', 'images/index.json', 'models/mineru-home/config.yaml'):
            with self.subTest(extra=extra):
                def change(rows):
                    record = next(row for row in members(self.payload) if row['path'] == extra)
                    return sorted(rows + [record], key=lambda row: row['path'])
                self.refuses(allow=self.allow(change=change))

    def test_mixed_case_acquisition_directory_cannot_bypass_exclusion(self):
        (self.payload / 'wheelhouse').rename(self.payload / 'Wheelhouse')
        self.seal()
        def change(rows):
            extra = next(row for row in members(self.payload) if row['path'].startswith('Wheelhouse/'))
            return sorted(rows + [extra], key=lambda row: row['path'])
        self.refuses(allow=self.allow(change=change))

    def test_extra_addon_metadata_and_unsupported_card_refused(self):
        path = self.allow('embedder', 'turing')
        rows = runtime.read_json(path)
        rows += [row for row in members(self.payload) if row['path'] == 'evidence/receipt.json']
        path.write_text(canonical(sorted(rows, key=lambda row: row['path'])), encoding='ascii')
        self.refuses('embedder', 'turing', path)
        for gpu_class in ('cpu', 'tei-cpu', 'spark121', 'unknown', None):
            with self.assertRaises(ValueError):
                runtime.expected_roles('embedder', gpu_class)
        with self.assertRaises(ValueError):
            runtime.expected_roles('core', 'turing')

    def test_wrong_image_identity_refused(self):
        self.pins['runtime']['reranker_image_id'] = 'sha256:' + 'c' * 64
        self.seal()
        self.refuses()

    def test_corrupt_archive_with_resealed_payload_refused_by_binding(self):
        self.write('images/reranker.tar', b'invented corrupt archive')
        next(row for row in self.images if row['role'] == 'reranker')['sha256'] = digest(self.payload / 'images/reranker.tar')
        self.seal()
        self.refuses()

    def test_duplicate_image_roles_refused(self):
        self.images.append(copy.deepcopy(self.images[0]))
        self.seal()
        self.refuses()

    def test_existing_and_overlapping_outputs_refused(self):
        existing = self.root / 'existing'
        existing.mkdir()
        for output in (existing, self.stage / 'inside', self.root, self.kit / 'inside'):
            with self.subTest(output=output), self.assertRaises(ValueError):
                self.build(out=output)
        self.assertEqual(list(existing.iterdir()), [])

    def test_copy_failure_has_no_completion_receipt(self):
        with patch.object(runtime.shutil, 'copyfile', side_effect=OSError('invented copy interruption')):
            with self.assertRaises(OSError):
                self.build()
        self.assertFalse((self.root / 'output/RUNTIME.json').exists())

    def test_metadata_failure_has_no_completion_receipt(self):
        with patch.object(runtime.os, 'utime', side_effect=OSError('invented metadata failure')):
            with self.assertRaises(OSError):
                self.build()
        self.assertFalse((self.root / 'output/RUNTIME.json').exists())

    def test_receipt_publication_timestamp_failure_removes_marker(self):
        original = runtime.os.utime
        def fail_after_receipt(path, times):
            if Path(path) == self.root / 'output' and (Path(path) / 'RUNTIME.json').exists():
                raise OSError('invented final timestamp failure')
            return original(path, times)
        with patch.object(runtime.os, 'utime', side_effect=fail_after_receipt):
            with self.assertRaises(OSError):
                self.build()
        self.assertFalse((self.root / 'output/RUNTIME.json').exists())

    @unittest.skipUnless(os.name != 'nt', 'POSIX link fixture requires local symlink privilege')
    def test_linked_payload_refused(self):
        (self.payload / 'linked').symlink_to(self.payload / 'evidence/receipt.json')
        self.refuses()


if __name__ == '__main__':
    unittest.main()
