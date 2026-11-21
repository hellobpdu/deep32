"""Synthetic producer orchestration and source-seal checks; no host execution."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

BUILD = Path(__file__).resolve().parents[1] / 'build'


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


producer = load('alpha2_pipeline_under_test', BUILD / 'alpha2_disc3.py')
source = load('alpha2_source_under_test', BUILD / 'alpha2_source.py')


class SourceFixture:
    def __init__(self, root):
        self.checkout = root / 'checkout'
        self.receipt = root / 'output/source.json'
        self.files = []
        self.revision = 'a' * 40
        self.tree = 'b' * 40
        self.status = ''
        for scope in source.SCOPES:
            if scope.endswith(('.py', '.gpg', '.fingerprint')):
                name = scope
            else:
                name = scope + '/synthetic.py'
            self.files.append(name)
            path = self.checkout / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('SYNTHETIC PUBLIC INPUT\n', encoding='ascii')

    def git(self, checkout, *args):
        if args[0] == 'status':
            return self.status
        if args[0] == 'rev-parse':
            return (self.tree if args[-1] == 'HEAD^{tree}' else self.revision) + '\n'
        if args[0] == 'ls-files':
            return '\0'.join(self.files) + '\0'
        raise AssertionError(args)


class SourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.fixture = SourceFixture(Path(self.temp.name))
        self.git = patch.object(source, '_git', side_effect=self.fixture.git)
        self.git.start()
        self.addCleanup(self.git.stop)

    def test_clean_source_seals_and_verifies(self):
        receipt = source.seal_source(self.fixture.checkout, self.fixture.receipt)
        self.assertEqual(receipt['revision'], 'a' * 40)
        self.assertEqual(len(receipt['files']), len(source.SCOPES))
        self.assertEqual(source.verify_source(self.fixture.checkout, self.fixture.receipt), receipt)
        self.assertNotIn(str(self.fixture.checkout), self.fixture.receipt.read_text())

    def test_dirty_checkout_refuses_before_output(self):
        self.fixture.status = ' M src/gateway/synthetic.py\0'
        with self.assertRaisesRegex(ValueError, 'must be clean'):
            source.seal_source(self.fixture.checkout, self.fixture.receipt)
        self.assertFalse(self.fixture.receipt.parent.exists())

    def test_fixture_text_is_never_opened(self):
        name = 'src/release-tools/contracts/fixtures/valid/synthetic-contract.json'
        fixture = self.fixture.checkout / name
        fixture.parent.mkdir(parents=True)
        fixture.write_bytes(b'SYNTHETIC DATA')
        self.fixture.files.append(name)
        original_hash = source._hash
        def checked_hash(path):
            self.assertNotEqual(path, fixture)
            return original_hash(path)
        with patch.object(source, '_hash', side_effect=checked_hash):
            receipt = source.seal_source(self.fixture.checkout, self.fixture.receipt)
        self.assertIn(name, receipt['metadata_only_paths'])
        self.assertNotIn(name, [row['path'] for row in receipt['files']])

    def test_changed_bytes_refuse_even_when_git_reports_clean(self):
        source.seal_source(self.fixture.checkout, self.fixture.receipt)
        (self.fixture.checkout / self.fixture.files[0]).write_text('SYNTHETIC CHANGED\n', encoding='ascii')
        with self.assertRaisesRegex(ValueError, 'changed after sealing'):
            source.verify_source(self.fixture.checkout, self.fixture.receipt)

    def test_changed_revision_refuses(self):
        source.seal_source(self.fixture.checkout, self.fixture.receipt)
        self.fixture.revision = 'c' * 40
        with self.assertRaises(ValueError):
            source.verify_source(self.fixture.checkout, self.fixture.receipt)

    def test_changed_tree_refuses(self):
        source.seal_source(self.fixture.checkout, self.fixture.receipt)
        self.fixture.tree = 'd' * 40
        with self.assertRaises(ValueError):
            source.verify_source(self.fixture.checkout, self.fixture.receipt)

    def test_ignored_untracked_context_file_refuses(self):
        source.seal_source(self.fixture.checkout, self.fixture.receipt)
        (self.fixture.checkout / 'src/gateway/ignored-input.py').write_text('SYNTHETIC\n', encoding='ascii')
        with self.assertRaisesRegex(ValueError, 'untracked files'):
            source.verify_source(self.fixture.checkout, self.fixture.receipt)

    def test_untracked_cache_is_ignored(self):
        source.seal_source(self.fixture.checkout, self.fixture.receipt)
        cache = self.fixture.checkout / 'src/gateway/__pycache__/synthetic.pyc'
        cache.parent.mkdir()
        cache.write_bytes(b'SYNTHETIC CACHE')
        self.fixture.status = '?? src/gateway/__pycache__/synthetic.pyc\0'
        source.verify_source(self.fixture.checkout, self.fixture.receipt)

    def test_prohibited_name_refuses_before_any_file_hash(self):
        # Name-only refusal is tested without opening or generating protected
        # question/corpus material. This empty directory is synthetic.
        (self.fixture.checkout / 'src/gateway/q1234').mkdir()
        with patch.object(source, '_hash', side_effect=AssertionError('must not read content')):
            with self.assertRaisesRegex(ValueError, 'prohibited source name'):
                source.seal_source(self.fixture.checkout, self.fixture.receipt)

    def test_tracked_prohibited_name_refuses_before_hash(self):
        self.fixture.files.append('src/gateway/example.env')
        with patch.object(source, '_hash', side_effect=AssertionError('must not read content')):
            with self.assertRaisesRegex(ValueError, 'Prohibited'):
                source.seal_source(self.fixture.checkout, self.fixture.receipt)

    def test_receipt_inside_checkout_refuses(self):
        with self.assertRaisesRegex(ValueError, 'outside the source checkout'):
            source.seal_source(self.fixture.checkout, self.fixture.checkout / 'source.json')


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.args = SimpleNamespace(checkout=BUILD.parents[2], sealed_stage=root / 'sealed',
                                    out=root / 'output', epoch=1, release_gates=root / 'gates.json',
                                    sbom_inventories=root / 'inventories', assembly_manifest=None)
        self.guard = patch.object(producer, 'verify_source', return_value={'revision': 'a' * 40})
        self.verified = self.guard.start()
        self.addCleanup(self.guard.stop)
        for name in ('verify_kit', 'verify_stages'):
            guard = patch.object(producer, name, return_value=None, create=True)
            guard.start()
            self.addCleanup(guard.stop)

    def argv(self, *extra):
        result = []
        for key in ('checkout', 'sealed_stage', 'out', 'epoch', 'release_gates', 'sbom_inventories'):
            result += ['--' + key.replace('_', '-'), str(getattr(self.args, key))]
        return result + list(extra)

    def host_patches(self):
        return (patch.object(producer, 'os', SimpleNamespace(name='posix', umask=lambda value: 0o022)),
                patch.object(producer, 'sys', SimpleNamespace(platform='linux', stderr=io.StringIO())))

    def test_main_runs_phases_in_order(self):
        events = []
        patches = list(self.host_patches())
        for name in ('copy', 'rebuild', 'stage', 'images', 'metadata', 'finish'):
            patches.append(patch.object(producer, 'phase_' + name, side_effect=lambda args, phase=name: events.append(phase)))
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        self.assertEqual(producer.main(self.argv()), 0)
        self.assertEqual(events, ['copy', 'rebuild', 'stage', 'images', 'metadata', 'finish'])

    def test_failed_rebuild_stops_before_staging(self):
        patches = list(self.host_patches())
        for item in patches:
            item.start()
            self.addCleanup(item.stop)
        with patch.object(producer, 'phase_copy'), patch.object(producer, 'phase_rebuild', side_effect=ValueError('synthetic gate missing')), patch.object(producer, 'phase_stage') as stage:
            self.assertEqual(producer.main(self.argv()), 2)
        stage.assert_not_called()

    def test_pending_build_resumes_qualification(self):
        path = self.args.out / 'rebuild/BUILD.json'
        path.parent.mkdir(parents=True)
        path.write_text('{}', encoding='ascii')
        helper = SimpleNamespace(rebuild=unittest.mock.Mock(), qualify_and_merge=unittest.mock.Mock())
        with patch.object(producer, 'load_module', return_value=helper):
            producer.phase_rebuild(self.args)
        helper.rebuild.assert_not_called()
        helper.qualify_and_merge.assert_called_once_with(self.args.out / 'stage', self.args.checkout / 'src/gpu-pack',
                                                         self.args.out / 'rebuild', gates=self.args.release_gates)
        self.verified.assert_called_once_with(self.args.checkout, self.args.out / 'SOURCE.json')

    def test_existing_matching_merge_skips_rebuild_and_qualification(self):
        stage = self.args.out / 'stage'
        stage.mkdir(parents=True)
        (stage / 'pins.json').write_text('{}', encoding='ascii')
        (stage / 'PAYLOAD.json').write_text('[]', encoding='ascii')
        producer.write_json(self.args.out / 'rebuild/MERGE.json', {
            'status': 'qualified-and-merged', 'merged_pins_sha256': producer.digest(stage / 'pins.json'),
            'merged_payload_sha256': producer.digest(stage / 'PAYLOAD.json')})
        helper = SimpleNamespace(rebuild=unittest.mock.Mock(), qualify_and_merge=unittest.mock.Mock())
        with patch.object(producer, 'load_module', return_value=helper):
            producer.phase_rebuild(self.args)
        helper.rebuild.assert_not_called()
        helper.qualify_and_merge.assert_not_called()

    def test_missing_merge_refuses_before_allowlist_or_staging(self):
        with patch.object(producer, 'selected_allowlist') as allow, patch.object(producer.runtime, 'stage_runtime') as stage:
            with self.assertRaises(ValueError):
                producer.phase_stage(self.args)
        allow.assert_not_called()
        stage.assert_not_called()

    def test_wrong_merge_hash_refuses_before_staging(self):
        folder = self.args.out / 'stage'
        folder.mkdir(parents=True)
        (folder / 'pins.json').write_text('{}', encoding='ascii')
        (folder / 'PAYLOAD.json').write_text('[]', encoding='ascii')
        producer.write_json(self.args.out / 'rebuild/MERGE.json', {
            'status': 'qualified-and-merged', 'merged_pins_sha256': '0' * 64,
            'merged_payload_sha256': '0' * 64})
        with patch.object(producer.runtime, 'stage_runtime') as stage:
            with self.assertRaisesRegex(ValueError, 'does not match'):
                producer.phase_stage(self.args)
        stage.assert_not_called()

    def test_valid_merge_stages_all_components_and_repeat_core(self):
        folder = self.args.out / 'stage'
        folder.mkdir(parents=True)
        (folder / 'pins.json').write_text('{}', encoding='ascii')
        (folder / 'PAYLOAD.json').write_text('[]', encoding='ascii')
        producer.write_json(self.args.out / 'rebuild/MERGE.json', {
            'status': 'qualified-and-merged', 'merged_pins_sha256': producer.digest(folder / 'pins.json'),
            'merged_payload_sha256': producer.digest(folder / 'PAYLOAD.json')})
        def staged(*args):
            args[4].mkdir(parents=True)
            (args[4] / 'synthetic.txt').write_text('SYNTHETIC COMPONENT', encoding='ascii')
        with patch.object(producer, 'selected_allowlist', return_value=[]), patch.object(producer.runtime, 'stage_runtime', side_effect=staged) as stage:
            producer.phase_stage(self.args)
        self.assertEqual(stage.call_count, 10)
        outputs = [call.args[4].name for call in stage.call_args_list]
        self.assertEqual(set(outputs), set(producer.COMPONENTS) | {'core-repeat'})

    def test_images_publish_nine_images_and_tenth_repeat_separately(self):
        self.args.out.mkdir()
        def image(stage, kit, component, output, epoch):
            output.mkdir(parents=True)
            path = output / producer.image_name(component)
            path.write_bytes(('SYNTHETIC ' + component).encode('ascii'))
            return path
        with patch.object(producer, 'build_image', side_effect=image) as build:
            producer.phase_images(self.args)
        self.assertEqual(build.call_count, 10)
        self.assertEqual(len(list((self.args.out / 'images').iterdir())), 18)
        for component in producer.COMPONENTS:
            path = self.args.out / 'images' / producer.image_name(component)
            self.assertEqual(path.with_name(path.name + '.sha256').read_text(),
                             producer.digest(path) + '  ' + path.name + '\n')
        input_root = self.args.out / 'sbom-input-images'
        self.assertEqual({path.name for path in input_root.iterdir()}, set(producer.COMPONENTS) | {'core-repeat'})
        for component in (*producer.COMPONENTS, 'core-repeat'):
            actual = 'core' if component == 'core-repeat' else component
            folder = input_root / component
            image = folder / producer.image_name(actual)
            self.assertEqual({path.name for path in folder.iterdir()}, {image.name, 'IMAGE.json'})
            self.assertEqual(json.loads((folder / 'IMAGE.json').read_text()),
                             {'path': image.name, 'size': image.stat().st_size, 'sha256': producer.digest(image)})
            self.assertEqual(image.read_bytes(), (self.args.out / 'components' / component / image.name).read_bytes())

    def test_metadata_writes_complete_ledger_and_records_both_clean_runs(self):
        for component in (*producer.COMPONENTS, 'core-repeat'):
            folder = self.args.out / 'components' / component
            folder.mkdir(parents=True)
            actual = 'core' if component == 'core-repeat' else component
            (folder / producer.image_name(actual)).write_bytes(b'SYNTHETIC ISO')
            disjoint = self.args.out / 'sbom-input-images' / component
            disjoint.mkdir(parents=True)
            (disjoint / producer.image_name(actual)).write_bytes(b'SYNTHETIC ISO')
            producer.write_json(disjoint / 'IMAGE.json', {'path': producer.image_name(actual),
                'size': len(b'SYNTHETIC ISO'), 'sha256': hashlib.sha256(b'SYNTHETIC ISO').hexdigest()})
        def emit(*args, **kwargs):
            output = args[4]
            producer.write_json(output / 'metadata/SBOM-INDEX.json', {'subjects': [
                {'contents': [], 'gap': None, 'subject': {'kind': 'disc'},
                 'sbom': {'asset': 'synthetic.spdx.json', 'sha256': 'a' * 64}}]})
            return {'sbom': 'synthetic.spdx.json'}
        metadata = SimpleNamespace(
            emit_component_sbom=unittest.mock.Mock(side_effect=emit),
            verify_component_sbom=unittest.mock.Mock(side_effect=emit),
            record_build_run=unittest.mock.Mock(side_effect=lambda *args: args[4]),
            compare_core=unittest.mock.Mock(return_value={'status': 'proven', 'byte_comparison': {'equal': True}}))
        estimate = dict(download_bytes=13, media_file_bytes=20, installed_bytes=30, peak_bytes=63,
                        image_sizes=[], accounting='Synthetic accounting.')
        with patch.object(producer, 'load_module', return_value=metadata), patch.object(producer, 'sizes', return_value=estimate):
            producer.phase_metadata(self.args)
        ledger = json.loads((self.args.out / 'SIZES.json').read_text())
        self.assertEqual({row['id'] for row in ledger['images']}, set(producer.COMPONENTS))
        self.assertEqual(len(ledger['images']), 9)
        self.assertEqual(metadata.emit_component_sbom.call_count, 10)
        self.assertEqual([call.args[5] for call in metadata.record_build_run.call_args_list], ['core', 'core-repeat'])
        self.assertEqual(len(metadata.compare_core.call_args.kwargs['run_receipts']), 2)
        for call in metadata.emit_component_sbom.call_args_list:
            self.assertEqual(call.args[1].parent.parent.name, 'sbom-input-images')
        for call in metadata.record_build_run.call_args_list:
            self.assertEqual(call.args[3].parent.parent.name, 'components')

    def test_changed_original_stage_refuses_finish(self):
        self.args.sealed_stage.mkdir()
        source_file = self.args.sealed_stage / 'synthetic.txt'
        source_file.write_text('SYNTHETIC', encoding='ascii')
        producer.write_json(self.args.out / 'SEALED-SOURCE.json', producer.members(self.args.sealed_stage))
        source_file.write_text('SYNTHETIC CHANGED', encoding='ascii')
        with self.assertRaisesRegex(ValueError, 'sealed stage changed'):
            producer.phase_finish(self.args)
        self.assertFalse((self.args.out / 'PRODUCER.json').exists())


class ImageAndSizesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_changed_component_pins_refuse_before_media_creation(self):
        stage, kit, output = self.root / 'stage', self.root / 'kit', self.root / 'output'
        (stage / 'payload').mkdir(parents=True)
        kit.mkdir()
        (stage / 'payload/synthetic.txt').write_text('SYNTHETIC PAYLOAD', encoding='ascii')
        producer.write_json(stage / 'PAYLOAD.json', producer.members(stage / 'payload'))
        producer.write_json(stage / 'pins.json', {'synthetic': 'original'})
        producer.write_json(stage / 'RUNTIME.json', {'component': 'embedder', 'gpu_class': 'turing',
            'roles': ['tei-turing'], 'payload_sha256': producer.digest(stage / 'PAYLOAD.json'),
            'pins_sha256': producer.digest(stage / 'pins.json')})
        producer.write_json(stage / 'pins.json', {'synthetic': 'changed'})
        with self.assertRaises(ValueError):
            producer.prepare_media(stage, kit, 'embedder-turing', output, 1)
        self.assertFalse(output.exists())


class PrivateCopySealTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.args = SimpleNamespace(out=self.root)

    def test_changed_private_kit_refuses(self):
        kit = self.root / 'kit'
        kit.mkdir()
        path = kit / 'synthetic.py'
        path.write_text('SYNTHETIC ORIGINAL', encoding='ascii')
        producer.write_json(self.root / 'KIT.json', producer.members(kit))
        producer.verify_kit(self.args)
        path.write_text('SYNTHETIC CHANGED', encoding='ascii')
        with self.assertRaises(ValueError):
            producer.verify_kit(self.args)

    def test_changed_derived_component_stage_refuses(self):
        stages = self.root / 'stages'
        (stages / 'core').mkdir(parents=True)
        path = stages / 'core/synthetic.txt'
        path.write_text('SYNTHETIC ORIGINAL', encoding='ascii')
        producer.write_json(self.root / 'STAGES.json', producer.members(stages))
        producer.verify_stages(self.args)
        path.write_text('SYNTHETIC CHANGED', encoding='ascii')
        with self.assertRaises(ValueError):
            producer.verify_stages(self.args)

    def test_image_build_verifies_and_records_exit_result(self):
        def media(stage, kit, component, output, epoch):
            output.mkdir(parents=True)
            (output / 'SYNTHETIC.txt').write_text('SYNTHETIC', encoding='ascii')

        def xorriso(command, **kwargs):
            image = next(Path(value) for value in command if value.endswith('.iso'))
            sector = producer.pack.SECTOR
            data = bytearray(19 * sector)
            for number, kind in ((16, 1), (17, 2), (18, 255)):
                start = number * sector
                data[start] = kind
                data[start + 1:start + 6] = b'CD001'
                data[start + 6] = 1
                if kind == 1:
                    data[start + 40:start + 72] = producer.pack.VOLUME.encode('ascii').ljust(32, b' ')
                elif kind == 2:
                    data[start + 88:start + 91] = b'%/E'
            image.write_bytes(data)
            return subprocess.CompletedProcess(command, 0)

        with patch.object(producer, 'prepare_media', side_effect=media):
            image = producer.build_image(self.root / 'stage', self.root / 'kit', 'core', self.root / 'out', 1, run=xorriso)
        report = json.loads((image.parent / 'pack-report.json').read_text())
        self.assertEqual(report['process_exit_results'], {'xorriso': 0})
        self.assertEqual(report['sha256'], producer.digest(image))
        self.assertEqual(report['boot_records'], 0)

    def test_sizes_use_inspected_layer_bytes(self):
        stage, media, image = self.root / 'stage', self.root / 'media', self.root / 'image.iso'
        archive = stage / 'payload/images/door.tar'
        archive.parent.mkdir(parents=True)
        archive.write_bytes(b'12345')
        identity = 'sha256:' + 'a' * 64
        producer.write_json(stage / 'payload/images/index.json', [{'role': 'door', 'path': 'images/door.tar', 'config_digest': identity}])
        (media / 'deep32/payload/images').mkdir(parents=True)
        (media / 'deep32/payload/images/door.tar').write_bytes(b'12345')
        (media / 'runtime.txt').write_bytes(b'1234')
        image.write_bytes(b'x' * 128)
        import image_binding
        with patch.object(image_binding, 'check_archive'), patch.object(image_binding, 'resolve_loaded', return_value=identity), patch.object(producer.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run, patch.object(producer.subprocess, 'check_output', return_value=json.dumps([{'Id': identity, 'Os': 'linux', 'Architecture': 'amd64', 'Size': 100}])):
            result = producer.sizes(stage, media, image)
        self.assertEqual(result['download_bytes'], 128)
        self.assertEqual(result['installed_bytes'], 104)
        self.assertEqual(result['peak_bytes'], 241)
        self.assertEqual(run.call_args.args[0][:3], ['docker', 'image', 'load'])

    def test_build_run_receipt_rechecks_complete_measured_fixture(self):
        metadata = producer.load_module('alpha2_metadata_pipeline_check', BUILD / 'alpha2_metadata.py')
        image = self.root / 'component' / producer.image_name('core')
        image.parent.mkdir()
        sector = producer.pack.SECTOR
        data = bytearray(19 * sector)
        for number, kind in ((16, 1), (17, 2), (18, 255)):
            start = number * sector
            data[start] = kind
            data[start + 1:start + 6] = b'CD001'
            data[start + 6] = 1
            if kind == 1:
                data[start + 40:start + 72] = producer.pack.VOLUME.encode('ascii').ljust(32, b' ')
            elif kind == 2:
                data[start + 88:start + 91] = b'%/E'
        image.write_bytes(data)
        media = image.parent / 'media'
        media.mkdir()
        (media / 'source.txt').write_text('SYNTHETIC SOURCE', encoding='ascii')
        (media / 'SHA256SUMS').write_text(producer.digest(media / 'source.txt') + '  source.txt\n', encoding='ascii')
        stage = self.root / 'stage'
        (stage / 'payload').mkdir(parents=True)
        (stage / 'payload/source.txt').write_text('SYNTHETIC SOURCE', encoding='ascii')
        producer.write_json(stage / 'pins.json', {'synthetic': True})
        producer.write_json(stage / 'PAYLOAD.json', producer.members(stage / 'payload'))
        report = dict(format='deep32-alpha2-component/1', sha256=producer.digest(image),
            size=image.stat().st_size, source_date_epoch=1, clean_build=True,
            clean_build_method='Separate new synthetic output.',
            cache_method='Synthetic producer uses no saved cache.',
            environment=dict(TZ='UTC', LC_ALL='C', umask='0022', SOURCE_DATE_EPOCH=1),
            process_exit_results={'xorriso': 0})
        report_path = image.parent / 'pack-report.json'
        producer.write_json(report_path, report)
        coverage = dict(format='deep32-alpha2-media-coverage/1', subject=image.name,
                        sha256=producer.digest(image), size=image.stat().st_size,
                        component_coverage='complete', media_files=1)
        coverage_path = self.root / 'sbom/COVERAGE.json'
        producer.write_json(coverage_path, coverage)
        index_path = coverage_path.parent / 'metadata/SBOM-INDEX.json'
        producer.write_json(index_path, {'subjects': [{'subject': dict(kind='disc', path=image.name,
                    size=image.stat().st_size, sha256=producer.digest(image))}]})
        producer.write_json(index_path.parent / 'METADATA.json', {'files': [dict(path=index_path.name,
                               size=index_path.stat().st_size, sha256=producer.digest(index_path))]})
        tools = self.root / 'tools'
        tools.mkdir()
        xorriso = tools / 'xorriso'
        interpreter = tools / 'python'
        xorriso.write_bytes(b'SYNTHETIC XORRISO BINARY')
        interpreter.write_bytes(b'SYNTHETIC PYTHON BINARY')
        measured = subprocess.CompletedProcess([], 0, stdout='xorriso version : 1.5.6\n', stderr='')
        with patch.object(metadata.shutil, 'which', return_value=str(xorriso)), patch.object(metadata, 'sys', SimpleNamespace(executable=str(interpreter), platform='synthetic-linux')), patch.object(metadata.platform, 'machine', return_value='x86_64'), patch.object(metadata.subprocess, 'run', return_value=measured) as tool:
            result = metadata.record_build_run(stage, 1, 'a' * 40, image, image.parent / 'RUN.json',
                                                'core', report_path, coverage_path)
        receipt = json.loads(result.read_text())
        self.assertEqual(receipt['process_exit_results'], {'xorriso': 0})
        self.assertTrue(all(receipt['verification'].values()))
        self.assertEqual(receipt['tool_versions']['xorriso'], '1.5.6')
        self.assertNotIn(str(self.root), result.read_text())
        self.assertEqual(tool.call_args.args[0], [str(xorriso), '-version'])


if __name__ == '__main__':
    unittest.main()
