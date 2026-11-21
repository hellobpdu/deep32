"""Retained runtime tests with synthetic files; no daemon or network access."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
KIT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('tested_installed_runtime', KIT / 'forms/installed_runtime.py')
installed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installed)
SCRATCH = os.environ.get('D32_SUITE_SCRATCH')


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True) + '\n', encoding='ascii')


def record(path, root):
    return dict(path=path.relative_to(root).as_posix(), size=path.stat().st_size,
                sha256=hashlib.sha256(path.read_bytes()).hexdigest())


class RetainedRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='synthetic-retention-', dir=SCRATCH)
        self.addCleanup(self.temp.cleanup)
        self.work = Path(self.temp.name)
        self.media = self.work / 'media'
        self.payload = self.media / 'payload'
        self.kit = self.work / 'kit'
        self.release = self.work / 'installation/releases' / ('f' * 64)
        self.payload.mkdir(parents=True)
        self.kit.mkdir()
        self.provision = installed._provision()
        runtime = self.provision.runtime_kit(KIT)
        for name in ('LICENSE', 'NOTICE', 'pins.json', 'build/thin_forms.py', 'build/provenance.py',
                     'firstboot/synthetic.py', 'forms/synthetic.py'):
            path = self.kit / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if name == 'pins.json':
                dump(path, {'release_profile': 'pack'})
            elif name == 'build/thin_forms.py':
                path.write_bytes((KIT / name).read_bytes())
            else:
                path.write_text('# Synthetic runtime fixture\n', encoding='ascii')
        for name in ('pairing/bin/deep32-pair', 'pairing/bin/pairing-helper',
                     *('pairing/deep32net/' + name + '.py' for name in runtime.PAIRING_MODULES)):
            path = self.kit / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('# Synthetic pairing fixture\n', encoding='ascii')
        self.images = {name: 'sha256:' + str(number) * 64 for number, name in
                       enumerate(('TEI_IMAGE', 'RERANKER_IMAGE', 'DOOR_IMAGE'), 1)}
        index = []
        for number, (role, key) in enumerate((('tei-turing', 'TEI_IMAGE'), ('reranker', 'RERANKER_IMAGE'),
                                             ('door', 'DOOR_IMAGE')), 1):
            archive = self.payload / ('images/' + role + '.tar')
            archive.parent.mkdir(exist_ok=True)
            # Deliberately not a valid archive: retention never opens archives.
            archive.write_bytes(b'Synthetic measured image ' + bytes([number]))
            index.append(dict(role=role, path=archive.relative_to(self.payload).as_posix(),
                              sha256=hashlib.sha256(archive.read_bytes()).hexdigest(), config_digest=self.images[key]))
        dump(self.payload / 'images/index.json', index)
        for name in ('models/tei/model.safetensors', 'models/tei/config.json',
                     'models/reranker/model.safetensors', 'models/mineru-home/models/snapshot/weights.bin',
                     'wheelhouse/build-only.whl', 'licences/source-note.txt'):
            path = self.payload / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(('Synthetic fixture: ' + name).encode('ascii'))
        self.seal()
        self.addCleanup(patch.stopall)
        patch.object(subprocess, 'Popen', side_effect=AssertionError('No real process')).start()
        patch.object(subprocess, 'run', side_effect=AssertionError('No real process')).start()

    def seal(self):
        rows = [record(path, self.payload) for path in sorted(self.payload.rglob('*')) if path.is_file()]
        dump(self.media / 'PAYLOAD.json', rows)
        (self.media / 'PAYLOAD.SHA256').write_text(''.join(row['sha256'] + '  ' + row['path'] + '\n' for row in rows),
                                                encoding='ascii')

    def prepare(self, prefixes=('models/tei/', 'models/reranker/'), **keywords):
        return installed.prepare(self.payload, self.kit, self.release, self.images, prefixes,
                                 selection='turing', **keywords)

    def test_retains_only_selected_models_kit_original_inventory_and_index(self):
        receipt = self.prepare()
        retained = {p.relative_to(self.release / 'payload').as_posix() for p in
                    (self.release / 'payload').rglob('*') if p.is_file()}
        self.assertEqual(retained, {'models/tei/model.safetensors', 'models/tei/config.json',
                                    'models/reranker/model.safetensors'})
        self.assertFalse(any(p.suffix == '.tar' for p in self.release.rglob('*')))
        self.assertEqual((self.release / 'installed-runtime/PAYLOAD.json').read_bytes(),
                         (self.media / 'PAYLOAD.json').read_bytes())
        self.assertEqual(receipt['images'], self.images)
        self.assertEqual(receipt['selection'], 'turing')
        self.assertEqual(installed.verify(self.release), receipt)

    def test_verify_after_media_and_source_kit_relocated(self):
        receipt = self.prepare()
        self.media.rename(self.work / 'ejected-media')
        self.kit.rename(self.work / 'ejected-kit')
        self.assertEqual(installed.verify(self.release, receipt['identity'], receipt['release']), receipt)

    def test_same_prepare_is_idempotent(self):
        expected = self.prepare()
        before = {p.relative_to(self.release).as_posix(): p.read_bytes() for p in self.release.rglob('*') if p.is_file()}
        self.assertEqual(self.prepare(), expected)
        self.assertEqual(before, {p.relative_to(self.release).as_posix(): p.read_bytes()
                                 for p in self.release.rglob('*') if p.is_file()})

    def select_converter(self):
        image = self.payload / 'images/converter.tar'
        image.write_bytes(b'Synthetic verified converter image')
        self.images['CONVERTER_IMAGE'] = 'sha256:' + '4' * 64
        index = self.payload / 'images/index.json'
        entries = json.loads(index.read_text(encoding='ascii'))
        entries.append(dict(role='converter', path='images/converter.tar',
                            sha256=hashlib.sha256(image.read_bytes()).hexdigest(),
                            config_digest=self.images['CONVERTER_IMAGE']))
        dump(index, entries)
        self.seal()
        return ('models/tei/', 'models/reranker/', 'models/mineru-home/')

    def test_ingestion_model_root_explicitly_retained(self):
        receipt = self.prepare(self.select_converter())
        self.assertEqual(receipt['schema'], installed.MINERU_LAYOUT_SCHEMA)
        self.assertTrue(any(row['path'].startswith('models/mineru-home/') for row in receipt['files']))
        lock = self.release / 'payload' / installed.MINERU_LOCKS
        self.assertTrue(lock.is_dir())
        self.assertEqual(list(lock.iterdir()), [])
        self.assertEqual(receipt, installed.verify(self.release))
        self.assertEqual(receipt, self.prepare(tuple(receipt['model_prefixes'])))

    def test_converter_mount_is_exact_and_empty(self):
        self.prepare(self.select_converter())
        lock = self.release / 'payload' / installed.MINERU_LOCKS
        lock.rmdir()
        with self.assertRaisesRegex(ValueError, 'missing MinerU lock'):
            installed.verify(self.release)
        lock.write_text('synthetic unexpected file', encoding='ascii')
        with self.assertRaisesRegex(ValueError, 'exact empty MinerU lock'):
            installed.verify(self.release)
        lock.unlink()
        lock.mkdir()
        (lock / 'unexpected').mkdir()
        with self.assertRaisesRegex(ValueError, 'exact empty MinerU lock'):
            installed.verify(self.release)

    def test_late_interruption_recovers_owned_empty_mount(self):
        prefixes = self.select_converter()
        publish = installed._atomic_json

        def interrupted(path, value):
            if path.name == 'receipt.json':
                raise OSError('Synthetic interruption before commit')
            return publish(path, value)

        with patch.object(installed, '_atomic_json', interrupted), self.assertRaises(OSError):
            self.prepare(prefixes)
        lock = self.release / 'payload' / installed.MINERU_LOCKS
        self.assertTrue(lock.is_dir())
        self.assertFalse((self.release / 'installed-runtime/receipt.json').exists())
        self.assertEqual(self.prepare(prefixes), installed.verify(self.release))

    def test_v1_converter_receipt_remains_readable_without_mutation(self):
        prefixes = self.select_converter()
        original = self.prepare(prefixes)
        lock = self.release / 'payload' / installed.MINERU_LOCKS
        lock.rmdir()
        previous = dict(original, schema=installed.SCHEMA)
        for name in ('plan.json', 'receipt.json'):
            dump(self.release / 'installed-runtime' / name, previous)
        self.assertEqual(installed.verify(self.release), previous)
        with self.assertRaisesRegex(ValueError, 'same verified runtime selection'):
            self.prepare(prefixes)
        self.assertFalse(lock.exists())

    def test_retained_kit_carries_license_and_notice(self):
        receipt = self.prepare()
        for name in ('LICENSE', 'NOTICE'):
            with self.subTest(name=name):
                self.assertIn(name, {row['path'] for row in receipt['kit']})
                self.assertEqual((self.release / 'kit' / name).read_bytes(), (self.kit / name).read_bytes())
        for name in ('LICENSE', 'NOTICE'):
            with self.subTest(removed=name):
                target = self.release / 'kit' / name
                original = target.read_bytes()
                target.unlink()
                with self.assertRaises(ValueError):
                    installed.verify(self.release)
                target.write_bytes(original)
        self.assertEqual(installed.verify(self.release), receipt)

    def test_older_v1_and_v2_kits_without_license_and_notice_still_verify_read_only(self):
        # A kit from before the terms joined the runtime kit: its own thin_forms, no LICENSE or NOTICE.
        thin = self.kit / 'build/thin_forms.py'
        text = thin.read_text(encoding='ascii')
        older = text.replace("('LICENSE', 'NOTICE', 'pins.json',", "('pins.json',")
        self.assertNotEqual(older, text)
        thin.write_text(older, encoding='ascii')
        for name in ('LICENSE', 'NOTICE'):
            (self.kit / name).unlink()
        for schema, prefixes in ((installed.SCHEMA, None), (installed.MINERU_LAYOUT_SCHEMA, 'converter')):
            with self.subTest(schema=schema):
                if prefixes is not None:
                    self.release = self.release.with_name('e' * 64)
                    receipt = self.prepare(self.select_converter())
                else:
                    receipt = self.prepare()
                self.assertEqual(receipt['schema'], schema)
                self.assertFalse({'LICENSE', 'NOTICE'} & {row['path'] for row in receipt['kit']})
                before = {p.relative_to(self.release).as_posix(): p.read_bytes()
                          for p in self.release.rglob('*') if p.is_file()}
                self.assertEqual(installed.verify(self.release), receipt)
                self.assertEqual(before, {p.relative_to(self.release).as_posix(): p.read_bytes()
                                          for p in self.release.rglob('*') if p.is_file()})

    def test_archive_cannot_declare_a_mineru_lock_member(self):
        prefixes = self.select_converter()
        lock_member = self.payload / installed.MINERU_LOCKS / 'unexpected'
        lock_member.parent.mkdir()
        lock_member.write_text('synthetic unexpected locked content', encoding='ascii')
        self.seal()
        with self.assertRaisesRegex(ValueError, 'reserved MinerU lock'):
            self.prepare(prefixes)
        self.assertFalse(self.release.exists())

    def test_receipt_and_plan_cannot_drop_models_required_by_selected_image_roles(self):
        receipt = self.prepare()
        for prefix in ('models/tei/', 'models/reranker/'):
            tampered = json.loads(json.dumps(receipt))
            tampered['model_prefixes'].remove(prefix)
            tampered['files'] = [row for row in tampered['files'] if not row['path'].startswith(prefix)]
            saved = self.release / 'payload' / prefix
            moved = self.work / ('removed-' + saved.name)
            saved.rename(moved)
            dump(self.release / 'installed-runtime/receipt.json', tampered)
            dump(self.release / 'installed-runtime/plan.json', tampered)
            with self.subTest(prefix=prefix), self.assertRaises(ValueError):
                installed.verify(self.release, expected_release=receipt['release'])
            moved.rename(saved)
        dump(self.release / 'installed-runtime/receipt.json', receipt)
        dump(self.release / 'installed-runtime/plan.json', receipt)

    def test_unknown_empty_duplicate_and_non_directory_model_selection_refuse_without_writes(self):
        for values in ((), ('models/',), ('models/tei/model.safetensors',), ('models/tei/', 'models/tei/')):
            with self.subTest(values=values), self.assertRaises(ValueError):
                self.prepare(values)
        self.assertFalse(self.release.exists())

    def test_missing_selected_model_root_refuses(self):
        shutil.rmtree(self.payload / 'models/tei')
        self.seal()
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.release.exists())

    def test_changed_source_model_refuses_before_writes(self):
        (self.payload / 'models/tei/model.safetensors').write_bytes(b'Changed synthetic model')
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.release.exists())

    def test_current_model_mutation_and_removal_refuse(self):
        self.prepare()
        model = self.release / 'payload/models/tei/model.safetensors'
        original = model.read_bytes()
        model.write_bytes(b'Changed synthetic model')
        with self.assertRaises(ValueError):
            installed.verify(self.release)
        model.write_bytes(original)
        model.unlink()
        with self.assertRaises(ValueError):
            installed.verify(self.release)

    def test_current_runtime_and_pins_mutation_refuse(self):
        self.prepare()
        for name in ('forms/synthetic.py', 'pins.json'):
            target = self.release / 'kit' / name
            original = target.read_bytes()
            target.write_bytes(b'Changed synthetic runtime')
            with self.subTest(name=name), self.assertRaises(ValueError):
                installed.verify(self.release)
            target.write_bytes(original)

    def test_mutated_original_inventory_index_and_receipt_refuse(self):
        self.prepare()
        for name in ('PAYLOAD.json', 'PAYLOAD.SHA256', 'index.json', 'receipt.json', 'plan.json'):
            target = self.release / 'installed-runtime' / name
            original = target.read_bytes()
            target.write_bytes(b'[]\n')
            with self.subTest(name=name), self.assertRaises((ValueError, TypeError)):
                installed.verify(self.release)
            target.write_bytes(original)

    def test_receipt_cannot_redefine_original_model_identity(self):
        self.prepare()
        target = self.release / 'installed-runtime/receipt.json'
        value = json.loads(target.read_text(encoding='ascii'))
        value['files'][0]['sha256'] = '0' * 64
        dump(target, value)
        dump(self.release / 'installed-runtime/plan.json', value)
        with self.assertRaises(ValueError):
            installed.verify(self.release)

    def test_expected_authenticated_identity_and_release_must_match(self):
        self.prepare()
        for arguments in ({'expected_identity': '0' * 64 + ':' + '1' * 64}, {'expected_release': '0' * 64}):
            with self.subTest(arguments=arguments), self.assertRaises(ValueError):
                installed.verify(self.release, **arguments)

    def test_model_cache_writes_extra_kit_and_archive_files_refuse(self):
        self.prepare()
        for name in ('payload/models/tei/cache/generated.json', 'payload/images/old.tar', 'kit/forms/extra.py'):
            target = self.release / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b'Synthetic unexpected file')
            with self.subTest(name=name), self.assertRaises(ValueError):
                installed.verify(self.release)
            target.unlink()
            if target.parent.name in ('cache', 'images'):
                target.parent.rmdir()

    def test_missing_receipt_is_never_a_complete_installation(self):
        self.prepare()
        (self.release / 'installed-runtime/receipt.json').unlink()
        with self.assertRaises(ValueError):
            installed.verify(self.release)
        self.assertEqual(self.prepare(), installed.verify(self.release))

    def test_partial_model_copy_has_no_receipt_and_resumes_same_transaction(self):
        original = self.provision.shutil.copyfile
        def interrupted(source, destination):
            if str(destination).endswith('model.safetensors.deep32-new'):
                Path(destination).write_bytes(b'Partial synthetic bytes')
                raise OSError('Synthetic copy interruption')
            return original(source, destination)
        with patch.object(installed, '_provision', return_value=self.provision), \
                patch.object(self.provision.shutil, 'copyfile', side_effect=interrupted):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertFalse((self.release / 'installed-runtime/receipt.json').exists())
        with self.assertRaises(ValueError):
            installed.verify(self.release)
        receipt = self.prepare()
        self.assertEqual(installed.verify(self.release), receipt)
        self.assertFalse(list(self.release.rglob('*.deep32-new')))

    def test_crash_before_and_after_atomic_receipt_commit(self):
        atomic = installed._atomic_json
        def interrupted(path, value):
            if path.name == 'receipt.json':
                raise OSError('Synthetic crash before commit')
            return atomic(path, value)
        with patch.object(installed, '_atomic_json', side_effect=interrupted), self.assertRaises(OSError):
            self.prepare()
        self.assertFalse((self.release / 'installed-runtime/receipt.json').exists())
        receipt = self.prepare()
        self.media.rename(self.work / 'removed-media-after-commit')
        self.assertEqual(installed.verify(self.release), receipt)

    def test_partial_copy_rejects_different_selected_models_and_wrong_media(self):
        atomic = installed._atomic_json
        def interrupted(path, value):
            atomic(path, value)
            if path.name == 'plan.json':
                raise OSError('Synthetic crash after plan')
        with patch.object(installed, '_atomic_json', side_effect=interrupted), self.assertRaises(OSError):
            self.prepare()
        with self.assertRaises(ValueError):
            self.prepare(('models/tei/',))
        (self.payload / 'models/tei/config.json').write_bytes(b'Different synthetic release')
        self.seal()
        with self.assertRaises(ValueError):
            self.prepare()

    def test_previous_release_is_unchanged(self):
        previous = self.release.parent / ('e' * 64) / 'retained-data.bin'
        previous.parent.mkdir(parents=True)
        previous.write_bytes(b'Previous synthetic generation')
        before = previous.read_bytes()
        self.prepare()
        self.assertEqual(previous.read_bytes(), before)

    def test_copied_models_are_flushed_before_rename_and_receipt_is_committed_last(self):
        events = []
        sync = os.fsync
        replace = Path.replace
        def synced(descriptor):
            events.append(('sync', descriptor))
            return sync(descriptor)
        def renamed(path, destination):
            if path.is_relative_to(self.release):
                if path.is_relative_to(self.release / 'payload'):
                    self.assertTrue(events and events[-1][0] == 'sync')
                events.append(('rename', Path(destination).relative_to(self.release).as_posix()))
            return replace(path, destination)
        with patch.object(os, 'fsync', side_effect=synced), patch.object(Path, 'replace', renamed):
            self.prepare()
        renamed_files = [value for event, value in events if event == 'rename']
        self.assertEqual(renamed_files[-1], 'installed-runtime/receipt.json')
        self.assertEqual(len([name for name in renamed_files if name.startswith('payload/models/')]), 3)

    def test_invalid_image_id_class_and_index_identity_refuse_before_writes(self):
        self.images['DOOR_IMAGE'] = 'mutable:latest'
        with self.assertRaises(ValueError):
            self.prepare()
        self.images['DOOR_IMAGE'] = 'sha256:' + '0' * 64
        with self.assertRaises(ValueError):
            self.prepare()
        with self.assertRaises(ValueError):
            installed.prepare(self.payload, self.kit, self.release, self.images,
                              ('models/tei/',), selection='tei-cpu')
        self.assertFalse(self.release.exists())

    def test_selected_source_archive_cannot_be_retained_as_model(self):
        archive = self.payload / 'models/tei/hidden.tar'
        archive.write_bytes(b'Synthetic archive')
        self.seal()
        with self.assertRaises(ValueError):
            self.prepare()
        self.assertFalse(self.release.exists())

    def test_hard_linked_installed_model_refuses(self):
        self.prepare()
        model = self.release / 'payload/models/tei/model.safetensors'
        os.link(model, self.work / 'synthetic-hardlink')
        with self.assertRaises(ValueError):
            installed.verify(self.release)

    def test_reparse_file_attributes_refuse_without_native_link_creation(self):
        self.prepare()
        original = Path.lstat
        target = self.release / 'payload/models/tei/model.safetensors'
        def attributes(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if path == target:
                from types import SimpleNamespace
                return SimpleNamespace(st_mode=value.st_mode, st_nlink=value.st_nlink,
                                       st_file_attributes=0x400)
            return value
        with patch.object(Path, 'lstat', attributes), self.assertRaises(ValueError):
            installed.verify(self.release)

    def test_duplicate_json_fields_refuse(self):
        self.prepare()
        target = self.release / 'installed-runtime/receipt.json'
        target.write_text('{"schema":"first","schema":"second"}', encoding='ascii')
        with self.assertRaises(ValueError):
            installed.verify(self.release)

    def test_original_full_inventory_keeps_archive_budget_even_though_archives_absent(self):
        self.prepare()
        rows = json.loads((self.release / 'installed-runtime/PAYLOAD.json').read_text(encoding='ascii'))
        self.assertTrue(any(row['path'].endswith('.tar') for row in rows))
        self.assertGreater(sum(row['size'] for row in rows),
                           sum(row['size'] for row in installed.verify(self.release)['files']))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch', type=Path, required=True)
    arguments, remaining = parser.parse_known_args()
    SCRATCH = arguments.scratch
    if not SCRATCH.is_dir() or any(SCRATCH.iterdir()):
        parser.error('--scratch must be an empty dedicated synthetic-test directory')
    unittest.main(argv=[sys.argv[0], *remaining])
