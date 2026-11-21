"""Offline synthetic metadata and comparison qualification."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

BUILD = Path(__file__).resolve().parents[1] / 'build'
sys.path.insert(0, str(BUILD))
import alpha2_metadata as metadata
import model_cache
from provenance import canonical, digest, members

EPOCH = 946684800
REVISION = '1' * 40


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canonical(value), encoding='ascii', newline='\n')


def iso(path, trailer=b''):
    path.parent.mkdir(parents=True, exist_ok=True)
    blocks = [bytearray(2048) for _ in range(19)]
    for block, kind in zip(blocks[16:], (1, 2, 255)):
        block[0] = kind
        block[1:6] = b'CD001'
        block[6] = 1
    blocks[16][40:72] = metadata.pack.VOLUME.encode('ascii').ljust(32, b' ')
    blocks[17][88:91] = b'%/E'
    path.write_bytes(b''.join(blocks) + trailer)


class MetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='alpha2-metadata-')
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.stage, self.media = self.base / 'stage', self.base / 'media'
        payload = self.stage / 'payload'
        (payload / 'images').mkdir(parents=True)
        (payload / 'source.txt').write_bytes(b'Synthetic public input.\n')
        write(payload / 'images/index.json', [])
        write(self.stage / 'pins.json', {'synthetic': True})
        write(self.stage / 'PAYLOAD.json', members(payload))
        shutil.copytree(payload, self.media / 'deep32/payload')
        shutil.copyfile(self.stage / 'PAYLOAD.json', self.media / 'deep32/PAYLOAD.json')
        (self.media / 'SHA256SUMS').write_bytes(b'Synthetic generated checksum fixture.\n')
        self.image = self.base / 'images/core.iso'
        iso(self.image)
        write(self.image.parent / 'manifest.json', {'synthetic': True})
        self.plan_path = self.base / 'plans/core.json'
        self.plan_path.parent.mkdir()
        self.plan = self.make_plan()
        write(self.plan_path, self.plan)

    def make_plan(self):
        roots = {'media': self.media, 'images': self.image.parent}
        inventory = [dict(id=name, files=members(root)) for name, root in roots.items()]
        components = []
        frozen_components = []
        gaps = []
        for number, row in enumerate(members(self.media)):
            if row['path'] == 'SHA256SUMS':
                continue
            name = 'file-' + str(number)
            components.append(dict(id=name, kind='data', name=name, version='1.0', architecture='all',
                                   usage='runtime', files=[dict(root='media', path=row['path'])],
                                   dependencies=[], licence_declared='NOASSERTION'))
            frozen_components.append(dict(id=name, name=name, version='1.0', purpose='FILE', scope='runtime',
                                          sha256=row['sha256'], source_url='NOASSERTION', licence='NOASSERTION',
                                          licence_evidence=[], dependencies=[], base_inventory=None))
            gaps.append(dict(id=name + '-license', target=name, field='licence',
                             reason='Synthetic input has no license declaration.'))
        ids = [c['id'] for c in components]
        subject = dict(id='core', kind='disc', file=dict(root='images', path=self.image.name),
                       role='shared-core', aliases=[], components=list(ids),
                       component_coverage='complete', licence_coverage='partial')
        manifest = dict(format='deep32-sbom-input/1', release=metadata.RELEASE,
                        source_revision=REVISION, source_date_epoch=EPOCH,
                        generator_sha256=metadata.generator_sha256(), roots=inventory,
                        components=components, subjects=[subject],
                        gaps=dict(format='deep32-sbom-gaps/1', gaps=gaps))
        refs = {name: dict(path=name + '/' + ('deep32/PAYLOAD.json' if name == 'media' else 'manifest.json'),
                           sha256=digest(root / ('deep32/PAYLOAD.json' if name == 'media' else 'manifest.json')))
                for name, root in roots.items()}
        contract = dict(format='deep32-sbom-input', format_version=1, release=metadata.RELEASE,
                        source_commit=REVISION, source_date_epoch=EPOCH,
                        generator_sha256=metadata.generator_sha256(),
                        input_roots=[dict(id=name, path=name, manifest=refs[name]) for name in roots],
                        subjects=[dict(kind='disc', path=self.image.name, size=self.image.stat().st_size,
                                       sha256=digest(self.image), aliases=[], input_manifests=list(refs.values()),
                                       components=list(ids), contents_sboms=[])], components=frozen_components,
                        exceptions=[], excluded_metadata=['SHA256SUMS'])
        return dict(format=metadata.PLAN, manifest=manifest, contract=contract,
                    roots=[dict(id='media', source='media', path='.'),
                           dict(id='images', source='image-parent', path='.')])

    def emit(self, output='output'):
        write(self.plan_path, self.plan)
        return metadata.emit_component_sbom(metadata.REPO, self.image, self.media, self.stage,
                                             self.base / output, EPOCH, REVISION,
                                             inventory_path=self.plan_path)

    def model_reference_media(self):
        pins = {'models': {
            'tei': {'id': 'invented/tei', 'revision': 'a' * 40},
            'reranker': {'id': 'invented/reranker', 'revision': 'b' * 40}}}
        references = model_cache.reference_files(pins)
        self.assertEqual(len(references), 4)
        write(self.stage / 'pins.json', pins)
        for name, value in references.items():
            path = self.stage / 'payload' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(value)
            self.assertEqual(value, pins['models'][name.split('/')[1]]['revision'].encode('ascii'))
        write(self.stage / 'PAYLOAD.json', members(self.stage / 'payload'))
        shutil.rmtree(self.media)
        shutil.copytree(self.stage / 'payload', self.media / 'deep32/payload')
        shutil.copyfile(self.stage / 'PAYLOAD.json', self.media / 'deep32/PAYLOAD.json')
        self.plan = self.make_plan()
        return references

    def run_receipt(self, name, image):
        identity = metadata.input_identity(self.stage, EPOCH, REVISION)
        toolchain = dict(platform='synthetic-linux', architecture='amd64',
                         tool_versions={'python': '3.15.0', 'xorriso': '1.5.6'})
        row = dict(format='deep32-alpha2-build-run/1', run_id=name, clean_build=True,
                   clean_build_method='Separate new synthetic output folders.',
                   cache_method='No cache used by synthetic fixtures.',
                   input_manifest_sha256=identity['sha256'], recipe_sha256=metadata.recipe_identity(),
                   toolchain_sha256=metadata._hash(toolchain), **toolchain,
                   environment=dict(TZ='UTC', LC_ALL='C', umask='0022', SOURCE_DATE_EPOCH=EPOCH),
                   process_exit_results={'assembly': 0},
                   verification=dict(data_only_iso=True, media_manifest=True, sbom_coverage=True),
                   output=dict(path=image.name, size=image.stat().st_size, sha256=digest(image)))
        path = self.base / (name + '.json')
        write(path, row)
        return path

    def test_external_emitter_is_deterministic_and_unknown_licenses_have_gaps(self):
        before = {row['path']: (self.media / row['path']).read_bytes() for row in members(self.media)}
        first = self.emit('output-one')
        second = self.emit('output-two')
        self.assertEqual(first, second)
        self.assertEqual(before, {row['path']: (self.media / row['path']).read_bytes() for row in members(self.media)})
        one, two = self.base / 'output-one/metadata', self.base / 'output-two/metadata'
        self.assertEqual({p.name: p.read_bytes() for p in one.iterdir()}, {p.name: p.read_bytes() for p in two.iterdir()})
        index = json.loads((one / 'SBOM-INDEX.json').read_bytes())
        self.assertEqual(index['subjects'][0]['coverage'], dict(components='complete', licences='partial'))
        self.assertIsNotNone(index['subjects'][0]['gap'])
        self.assertEqual(first['media_files'], 3)
        self.assertFalse(any(p.suffix == '.json' for p in self.media.iterdir()))

    def test_uncovered_media_file_refuses_before_publication(self):
        self.plan['manifest']['components'].pop()
        self.plan['contract']['components'].pop()
        self.plan['manifest']['subjects'][0]['components'].pop()
        self.plan['contract']['subjects'][0]['components'].pop()
        self.plan['manifest']['gaps']['gaps'].pop()
        with self.assertRaisesRegex(ValueError, 'explicit component or subject coverage'):
            self.emit()
        self.assertFalse((self.base / 'output').exists())

    def test_model_references_are_measured_in_external_sbom_and_contents(self):
        references = self.model_reference_media()
        stage_before, media_before = members(self.stage), members(self.media)
        emitted = self.emit()
        self.assertEqual(emitted['media_files'], len(members(self.media)))
        for name in ('core.spdx.json', 'core.contents.spdx.json'):
            document = json.loads((self.base / 'output/metadata' / name).read_bytes())
            files = {row['fileName']: row for row in document['files']}
            for relative, value in references.items():
                with self.subTest(document=name, reference=relative):
                    record = files['./media/deep32/payload/' + relative]
                    self.assertEqual(record['checksums'], [dict(
                        algorithm='SHA256', checksumValue=hashlib.sha256(value).hexdigest())])
                    self.assertEqual(record['comment'], 'Measured size: 40 bytes.')
        self.assertEqual(members(self.stage), stage_before)
        self.assertEqual(members(self.media), media_before)

    def test_omitted_model_reference_coverage_refuses_external_sbom(self):
        references = self.model_reference_media()
        omitted = 'deep32/payload/' + next(iter(references))
        component = next(row['id'] for row in self.plan['manifest']['components']
                         if any(file['path'] == omitted for file in row['files']))
        for field in ('manifest', 'contract'):
            self.plan[field]['components'] = [row for row in self.plan[field]['components']
                                              if row['id'] != component]
            self.plan[field]['subjects'][0]['components'].remove(component)
        self.plan['manifest']['gaps']['gaps'] = [
            row for row in self.plan['manifest']['gaps']['gaps'] if row['target'] != component]
        stage_before, media_before = members(self.stage), members(self.media)
        with self.assertRaisesRegex(ValueError, 'explicit component or subject coverage'):
            self.emit()
        self.assertFalse((self.base / 'output').exists())
        self.assertEqual(members(self.stage), stage_before)
        self.assertEqual(members(self.media), media_before)

    def test_changed_sealed_input_and_mismatched_final_image_are_refused(self):
        (self.media / 'deep32/payload/source.txt').write_bytes(b'Changed input.\n')
        with self.assertRaises(ValueError):
            self.emit()
        self.assertFalse((self.base / 'output').exists())
        shutil.copyfile(self.stage / 'payload/source.txt', self.media / 'deep32/payload/source.txt')
        self.plan['contract']['subjects'][0]['sha256'] = 'f' * 64
        with self.assertRaises(ValueError):
            self.emit()
        self.assertFalse((self.base / 'output').exists())

    def test_completed_component_verification_rejects_mutation_and_extra_assets(self):
        emitted = self.emit()
        def verify():
            return metadata.verify_component_sbom(metadata.REPO, self.image, self.media, self.stage,
                     self.base / 'output', EPOCH, REVISION, inventory_path=self.plan_path)
        self.assertEqual(emitted, verify())
        extra = self.base / 'output/unexpected.json'
        extra.write_bytes(b'{}\n')
        with self.assertRaisesRegex(ValueError, 'Completed component metadata differs'):
            verify()
        extra.unlink()
        asset = self.base / 'output/metadata/core.spdx.json'
        asset.write_bytes(b'{}\n')
        with self.assertRaisesRegex(ValueError, 'Completed component metadata differs'):
            verify()

    def test_unsafe_or_wrong_identity_inventory_refuses(self):
        original = copy.deepcopy(self.plan)
        for mutate in ('root', 'revision', 'generator'):
            with self.subTest(mutate=mutate):
                self.plan = copy.deepcopy(original)
                if mutate == 'root':
                    self.plan['roots'][0]['path'] = '../media'
                elif mutate == 'revision':
                    self.plan['manifest']['source_revision'] = 'f' * 40
                else:
                    self.plan['manifest']['generator_sha256'] = 'f' * 64
                with self.assertRaises(ValueError):
                    self.emit()
                self.assertFalse((self.base / 'output').exists())

    def test_missing_installed_container_inventory_never_silently_passes(self):
        write(self.stage / 'payload/images/index.json', [dict(role='door', path='images/door.tar', sha256='a' * 64)])
        (self.stage / 'payload/images/door.tar').write_bytes(b'Synthetic opaque archive.\n')
        write(self.stage / 'PAYLOAD.json', members(self.stage / 'payload'))
        shutil.rmtree(self.media)
        shutil.copytree(self.stage / 'payload', self.media / 'deep32/payload')
        shutil.copyfile(self.stage / 'PAYLOAD.json', self.media / 'deep32/PAYLOAD.json')
        self.plan = self.make_plan()
        with self.assertRaisesRegex(ValueError, 'Saved container coverage'):
            self.emit()
        self.assertFalse((self.base / 'output').exists())

    def test_equal_bytes_without_clean_run_evidence_are_not_proven(self):
        second = self.base / 'second/core.iso'
        iso(second)
        row = metadata.compare_core(self.image, second, self.base / 'comparison', EPOCH, REVISION, self.stage)
        self.assertTrue(row['byte_comparison']['equal'])
        self.assertEqual(row['status'], 'not-proven')
        statement = (self.base / 'comparison/REPRODUCIBILITY.txt').read_text(encoding='ascii')
        self.assertNotIn('were byte identical', statement)
        self.assertNotIn(str(self.base), (self.base / 'comparison/core-comparison.receipt.json').read_text())

    def test_proven_only_with_matching_complete_clean_run_receipts(self):
        second = self.base / 'second/core.iso'
        iso(second)
        receipts = (self.run_receipt('run-a', self.image), self.run_receipt('run-b', second))
        result = metadata.compare_core(self.image, second, self.base / 'comparison', EPOCH, REVISION,
                                        self.stage, run_receipts=receipts)
        self.assertEqual(result['status'], 'proven')
        receipt = json.loads((self.base / 'comparison/core-comparison.receipt.json').read_bytes())
        self.assertEqual(receipt['scope'], 'media-assembly')
        self.assertEqual(len(receipt['runs']), 2)
        self.assertEqual(receipt['outputs'][0]['sha256'], digest(self.image))
        self.assertEqual((self.base / 'comparison/REPRODUCIBILITY.txt').read_text().splitlines(), [
            'The shared core was assembled in two recorded clean runs from the same sealed inputs and toolchain.',
            'The two final ISO files were byte identical.',
            'This checks media assembly on the recorded toolchain; upstream images and model training are not proven '
            'reproducible.'])

    def test_changed_bytes_and_failed_coverage_are_not_proven(self):
        second = self.base / 'second/core.iso'
        iso(second, b'changed final image')
        a, b = self.run_receipt('run-a', self.image), self.run_receipt('run-b', second)
        row = json.loads(b.read_bytes())
        row['verification']['sbom_coverage'] = False
        write(b, row)
        result = metadata.compare_core(self.image, second, self.base / 'comparison', EPOCH, REVISION,
                                        self.stage, run_receipts=(a, b))
        self.assertEqual(result['status'], 'not-proven')
        self.assertFalse(result['byte_comparison']['equal'])
        self.assertEqual(result['byte_comparison']['exit_result'], 1)

    def test_same_output_or_same_run_identifiers_are_not_independent_builds(self):
        receipts = (self.run_receipt('run-a', self.image), self.run_receipt('run-b', self.image))
        row = metadata.compare_core(self.image, self.image, self.base / 'comparison', EPOCH, REVISION,
                                     self.stage, run_receipts=receipts)
        self.assertEqual(row['status'], 'not-proven')
        second = self.base / 'second/core.iso'
        iso(second)
        a, b = self.run_receipt('run-c', self.image), self.run_receipt('run-d', second)
        row = json.loads(b.read_bytes())
        row['run_id'] = 'run-c'
        write(b, row)
        result = metadata.compare_core(self.image, second, self.base / 'comparison-two', EPOCH, REVISION,
                                        self.stage, run_receipts=(a, b))
        self.assertEqual(result['status'], 'not-proven')

    def test_stale_receipts_and_excluded_stage_names_refuse(self):
        second = self.base / 'second/core.iso'
        iso(second)
        receipts = (self.run_receipt('run-a', self.image), self.run_receipt('run-b', second))
        iso(second, b'changed')
        with self.assertRaisesRegex(ValueError, 'measured final output'):
            metadata.compare_core(self.image, second, self.base / 'comparison', EPOCH, REVISION,
                                  self.stage, run_receipts=receipts)
        # A task-generated synthetic name exercises refusal before opening text.
        (self.stage / 'synthetic.env').write_bytes(b'SYNTHETIC TEST MATERIAL\n')
        with self.assertRaisesRegex(ValueError, 'excluded private input path'):
            metadata.input_identity(self.stage, EPOCH, REVISION)

    def test_cli_refuses_missing_inventory_without_echoing_operator_path(self):
        run = subprocess.run([sys.executable, '-I', '-B', str(BUILD / 'alpha2_metadata.py'), 'emit',
                              '--repo', str(metadata.REPO), '--image', str(self.image), '--media', str(self.media),
                              '--stage', str(self.stage), '--out', str(self.base / 'output'),
                              '--inventory', str(self.base / 'missing.json'), '--epoch', str(EPOCH),
                              '--source-revision', REVISION], capture_output=True)
        self.assertEqual(run.returncode, 2)
        self.assertIn(b'Metadata input is missing, unsafe or inconsistent.', run.stderr)
        self.assertNotIn(str(self.base).encode(), run.stderr)

    def test_frozen_expansion_has_every_subject_and_never_fabricates_absent_hashes(self):
        self.emit()
        second = self.base / 'second/core.iso'
        iso(second)
        receipts = (self.run_receipt('run-a', self.image), self.run_receipt('run-b', second))
        metadata.compare_core(self.image, second, self.base / 'comparison', EPOCH, REVISION,
                               self.stage, run_receipts=receipts)
        index = json.loads((self.base / 'output/metadata/SBOM-INDEX.json').read_bytes())
        other = copy.deepcopy(index['subjects'][0])
        other['subject']['path'] = 'disc2.iso'
        other['sbom']['asset'] = 'disc2.spdx.json'
        other['contents'] = []
        index['subjects'].append(other)
        value = metadata.expand_reproducibility(self.base / 'comparison/REPRODUCIBILITY.json', index, None)
        self.assertEqual(len(value['subjects']), 2)
        rows = {r['subject']['path']: r for r in value['subjects']}
        self.assertEqual(rows['core.iso']['status'], 'proven')
        self.assertEqual(len(rows['core.iso']['runs']), 2)
        self.assertEqual(rows['disc2.iso']['status'], 'not-proven')
        for key in ('input_manifest_sha256', 'recipe_sha256', 'toolchain_sha256'):
            self.assertIsNone(rows['disc2.iso'][key])
        self.assertEqual(rows['disc2.iso']['runs'], [])
        self.assertIsNone(rows['disc2.iso']['byte_comparison'])
        from contract_validate import validate_contract
        for status in ('proven', 'reused'):
            invalid = copy.deepcopy(value)
            invalid['subjects'][0]['status'] = status
            invalid['subjects'][0]['toolchain_sha256'] = None
            with self.assertRaisesRegex(ValueError, 'require recorded evidence hashes'):
                validate_contract('reproducibility', invalid)

    def test_record_actual_build_checks_measured_outputs_and_tool_versions(self):
        # Replace the invented checksum prose with an actual measured manifest.
        text = ''.join(r['sha256'] + '  ' + r['path'] + '\n' for r in members(self.media)
                       if r['path'] != 'SHA256SUMS')
        (self.media / 'SHA256SUMS').write_text(text, encoding='ascii', newline='\n')
        self.plan = self.make_plan()
        self.emit()
        # record_build_run follows the producer's fixed image-parent/media layout.
        shutil.copytree(self.media, self.image.parent / 'media')
        report = dict(format='deep32-alpha2-component/1', source_date_epoch=EPOCH,
                      size=self.image.stat().st_size, sha256=digest(self.image), clean_build=True,
                      clean_build_method='Fresh synthetic output directory.',
                      cache_method='No cache in the synthetic fixture.',
                      process_exit_results={'xorriso': 0},
                      environment=dict(TZ='UTC', LC_ALL='C', umask='0022', SOURCE_DATE_EPOCH=EPOCH))
        report_path = self.base / 'pack-report.json'
        write(report_path, report)
        binary = self.base / 'synthetic-xorriso'
        binary.write_bytes(b'Synthetic binary hash fixture; never executed.\n')
        with patch.object(metadata.shutil, 'which', return_value=str(binary)), patch.object(
                metadata.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, 'xorriso version : 1.5.6\n', '')):
            # The actual xorriso version line uses a colon; support both forms.
            path = metadata.record_build_run(self.stage, EPOCH, REVISION, self.image,
                                             self.base / 'actual-run.json', 'actual-run', report_path,
                                             self.base / 'output/COVERAGE.json')
        result = json.loads(path.read_bytes())
        self.assertEqual(result['output']['sha256'], digest(self.image))
        self.assertEqual(result['tool_versions']['xorriso_binary_sha256'], digest(binary))
        self.assertEqual(result['process_exit_results']['xorriso'], 0)
        self.assertTrue(result['verification']['sbom_coverage'])
        (self.image.parent / 'media/deep32/payload/source.txt').write_bytes(b'Changed.\n')
        with self.assertRaisesRegex(ValueError, 'checksum list'):
            metadata.record_build_run(self.stage, EPOCH, REVISION, self.image,
                                       self.base / 'bad-run.json', 'bad-run', report_path,
                                       self.base / 'output/COVERAGE.json')


if __name__ == '__main__':
    unittest.main(verbosity=2)
