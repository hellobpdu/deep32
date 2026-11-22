#!/usr/bin/env python3
"""Offline content compatibility and pointerless library regressions."""
import copy
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
IMPORTER = ROOT / 'src/workstation/distrib/site/usr/local/d32/libexec/d32-import'
sys.path.insert(0, str(IMPORTER))
import imp_fs
import imp_files
import imp_index
import imp_journal
import imp_manifest
import imp_paths
import imp_report
import imp_switch
import imp_units

FINGERPRINT = '0123456789ABCDEF0123456789ABCDEF01234567'
SYSTEM = '0.1.0-alpha.3'
COMPATIBLE = '0.1.0-alpha.2'
SHIPPED_COMPATIBILITY = ROOT / 'src/workstation/distrib/site/etc/d32/content-compatible-versions'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_row(name, data):
    return {'path': name, 'size': len(data), 'sha256': digest(data)}


def library_archive(pack, generation, prefix, inner_manifest=False):
    content = [(prefix + '/manifest.jsonl', b''), (prefix + '/paper.txt', b'TEST\n')]
    if inner_manifest:
        # A content file may itself be named MANIFEST.tsv (the alpha.1 corpus pack carries one).
        content.insert(0, (prefix + '/MANIFEST.tsv', b'inner\n'))
    record_prefix = 'var/db/d32/packs/' + pack + '/'
    info = ('name=' + pack + '\nclass=library\nrelease=' + generation + '\nrights=synthetic\n'
            'files=' + str(len(content)) + '\nbytes=' + str(sum(len(d) for _, d in content)) +
            '\nmade=1\ngeneration=' + generation + '\n').encode('ascii')
    covered = content + [(record_prefix + 'INFO', info)]
    manifest = ''.join(name + '\tf\t0440\troot\t_d32users\t' + str(len(data)) + '\t' +
                       digest(data) + '\tsynthetic\n' for name, data in covered).encode('ascii')
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for name, data in content + [(record_prefix + 'MANIFEST.tsv', manifest), (record_prefix + 'INFO', info)]:
            member = tarfile.TarInfo(name)
            member.uid = member.gid = 0
            member.uname, member.gname = 'root', '_d32users'
            member.mode, member.mtime, member.size = 0o440, 1, len(data)
            archive.addfile(member, io.BytesIO(data))
    return out.getvalue()


class ContentCompatibility(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='import-content-', dir=os.environ.get('D32_TEST_WORK'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {
            'D32_ROOT': str(self.root), 'D32_STUBS': str(self.root / 'stubs'),
            'D32_TEST_CLOCK': '946684800', 'D32_TEST_RAND': '01234567',
            'D32_TEST_FREE_BYTES': '1000000000', 'D32_TEST_POINTERS': 'linkfile',
            'D32_TEST_FAULTS': '', 'D32_TEST_CRASH': '', 'D32_TEST_SIGNAL_AT': '',
        })
        self.environment.start()
        self.addCleanup(self.environment.stop)
        imp_paths._HOOKS = None
        imp_paths._CLOCK_READS = 0
        imp_fs._SIGNALLED = False
        imp_fs._SIGNALS_IGNORED = False
        imp_fs._USED_WRITE_FAULTS.clear()
        self.addCleanup(setattr, imp_paths, '_HOOKS', None)
        self.counter = 0
        p = imp_paths.workstation()
        for folder in (p.cr, p.release_file.parent, p.release_key.parent, self.root / 'stubs'):
            folder.mkdir(parents=True, exist_ok=True)
        p.release_file.write_text(SYSTEM + '\n', encoding='ascii')
        p.content_compatible_versions.write_bytes(SHIPPED_COMPATIBILITY.read_bytes())
        p.release_key.write_bytes(b'SYNTHETIC PUBLIC KEY FIXTURE\n')
        p.release_fingerprint.write_text(FINGERPRINT + '\n', encoding='ascii')
        (self.root / 'stubs/gpgv.py').write_bytes(b'# SYNTHETIC VERIFIER FIXTURE\n')

    def context(self, accepts=(COMPATIBLE,), generation='papers-g1', pack='lib-cisco', pointer=None, inner_manifest=False):
        self.counter += 1
        p = imp_paths.workstation()
        group = 'encyclopedia' if pack == 'lib-encyclopedia' else 'cisco-papers'
        prefix = 'd32/libcontent/.' + group + '-' + generation
        raw_stream = library_archive(pack, generation, prefix, inner_manifest)
        part_path = 'library/' + pack + '/' + pack + '-' + generation + '.tar.part001'
        payloads = {'kb/test-kb/BUNDLE.json': b'{}\n', 'kb/test-kb/MANIFEST.json': b'{}\n', part_path: raw_stream}
        library = {
            'pack': pack, 'generation': generation, 'release': generation, 'program_pack': None,
            'pointer': pointer, 'install_prefix': prefix, 'kb_generation': None, 'kb_fingerprint': None,
            'has_cite_map': False, 'stream_bytes': len(raw_stream), 'stream_sha256': digest(raw_stream),
            'part_size': len(raw_stream),
            'parts': [{'index': 1, **file_row(part_path, raw_stream)}],
        }
        manifest = {
            'format': 'deep32-content-manifest', 'format_version': 1,
            'disc': {'role': 'content', 'version': accepts[0], 'volume_id': 'TEST_CONTENT', 'label': 'TEST',
                     'created': '2000-01-01T00:00:00Z', 'build_option': 'standard'},
            'accepts_system': sorted(accepts), 'signer': {'type': 'openpgp', 'fingerprint': FINGERPRINT},
            'kb': {'path': 'kb/test-kb', 'generation': 'test-kb', 'fingerprint': '0123456789abcdef',
                   'created': '2000-01-01T00:00:00Z', 'bundle_json_sha256': digest(b'{}\n'),
                   'manifest_sha256': digest(b'{}\n'), 'documents': 0, 'chunks': 0, 'points': 0,
                   'by_collection': {}},
            'blocks': [], 'library': [library], 'cisco_public': None, 'code': None, 'root_files': [],
            'members': [file_row(name, payloads[name]) for name in sorted(payloads)],
            'counts': {'files': len(payloads), 'bytes': sum(map(len, payloads.values()))},
        }
        raw = (json.dumps(manifest, indent=2) + '\n').encode('ascii')
        disc_root = self.root / ('disc-' + str(self.counter))
        for name, data in payloads.items():
            destination = disc_root.joinpath(*name.split('/'))
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        import_id = '20000101T000000Z-' + format(self.counter, '08x')
        registry = imp_journal.Registry(p)
        journal = imp_journal.Journal(p, load=False)
        journal.new(digest(raw), 'synthetic-disc', import_id, registry_before=copy.deepcopy(registry.data),
                    registry_existed_before=p.registry_file.exists())
        report = imp_report.Report(p, import_id, 'synthetic-disc')
        ctx = SimpleNamespace(p=p, import_id=import_id, registry=registry, journal=journal, report=report,
                              manifest=None, desired=[], stage_dir=None, current_stage='signature', current=False,
                              options={'qdrant_url': None},
                              disc=SimpleNamespace(root=disc_root, manifest_bytes=raw, signature_bytes=b'SYNTHETIC\n',
                                                   signature_error=None, label='TEST'),
                              run_child=lambda *a, **kw: SimpleNamespace(returncode=0, stdout='', stderr=''))
        return ctx

    def accept(self, ctx):
        imp_manifest.run_signature(ctx)
        # The regression exercises actual library payload stages. The synthetic KB
        # is manifest evidence only; vendor bundle validation is outside this test.
        ctx.desired = [row for row in ctx.desired if row['unit_kind'] == 'library']
        ctx.journal.data['desired'] = ctx.desired
        ctx.journal.save()
        return ctx

    def install(self, ctx):
        # The Workstation's stages: one read of the disc into checked copies, then install from them.
        self.accept(ctx)
        ctx.current_stage = 'files'
        imp_files.run_files(ctx)
        ctx.current_stage = 'space'
        imp_files.run_space(ctx)
        ctx.current_stage = 'library'
        imp_files.run_library(ctx)
        imp_switch.run_switch(ctx)
        ctx.report.data['kb']['embedding'] = {'model_id': 'test', 'revision': 'test', 'dimension': 1}
        imp_switch.run_check(ctx)
        ctx.report.data['stage'] = 'finish'
        imp_switch._cleanup(ctx, 'imported', False)
        return ctx

    def test_reserved_derived_names_have_no_pointer(self):
        for name in ('cisco', 'kb'):
            with self.subTest(name=name):
                self.assertIsNone(imp_manifest.group_pointer({'pointer': None, 'install_prefix': 'd32/libcontent/.' + name + '-papers-g1'}))

    def test_explicit_reserved_pointer_is_refused(self):
        for name in ('cisco', 'kb'):
            with self.subTest(name=name):
                ctx = self.context(pointer='/d32/libcontent/' + name)
                with self.assertRaises(imp_fs.ImportFailure) as failed:
                    imp_manifest.validate_manifest(ctx.disc.manifest_bytes, FINGERPRINT)
                self.assertEqual(failed.exception.code, 'IMPORT_MANIFEST')

    def test_encyclopedia_still_derives_pointer(self):
        ctx = self.accept(self.context(pack='lib-encyclopedia'))
        self.assertEqual(ctx.desired[0]['pointer'], 'encyclopedia')

    def test_content_file_named_manifest_is_covered(self):
        ctx = self.install(self.context(inner_manifest=True))
        self.assertEqual(ctx.report.unit('lib-cisco')['state'], 'installed')
        self.assertEqual((ctx.p.cr / ctx.desired[0]['target'] / 'MANIFEST.tsv').read_bytes(), b'inner\n')

    def test_alpha3_system_imports_alpha2_content(self):
        ctx = self.install(self.context())
        self.assertEqual(ctx.report.unit('lib-cisco')['state'], 'installed')
        self.assertEqual((ctx.p.cr / ctx.desired[0]['target'] / 'paper.txt').read_bytes(), b'TEST\n')
        self.assertEqual(ctx.registry.active['pointers'], {})
        self.assertFalse((ctx.p.cr / 'cisco').exists())

    def test_alpha1_content_is_no_longer_accepted(self):
        # alpha.1's images are withdrawn; the shipped list names only alpha.2 (lead ruling I-B2).
        with self.assertRaises(imp_fs.ImportFailure) as failed:
            imp_manifest.run_signature(self.context(accepts=('0.1.0-alpha.1',)))
        self.assertEqual(failed.exception.code, 'IMPORT_WRONG_SYSTEM')

    def test_own_system_version_still_imports(self):
        ctx = self.install(self.context(accepts=(SYSTEM,)))
        self.assertEqual(ctx.report.unit('lib-cisco')['state'], 'installed')

    def test_unlisted_content_version_is_refused(self):
        with self.assertRaises(imp_fs.ImportFailure) as failed:
            imp_manifest.run_signature(self.context(accepts=('0.1.0-alpha.0',)))
        self.assertEqual(failed.exception.code, 'IMPORT_WRONG_SYSTEM')

    def test_missing_compatibility_list_grants_no_extra_version(self):
        imp_paths.workstation().content_compatible_versions.unlink()
        with self.assertRaises(imp_fs.ImportFailure) as failed:
            imp_manifest.run_signature(self.context())
        self.assertEqual(failed.exception.code, 'IMPORT_WRONG_SYSTEM')

    def test_malformed_compatibility_list_grants_no_extra_version(self):
        for data in (b'0.1.0-alpha.2\ninvalid version\n', b'\xff\n', b'x' * 4097):
            with self.subTest(data_size=len(data)):
                imp_paths.workstation().content_compatible_versions.write_bytes(data)
                with self.assertRaises(imp_fs.ImportFailure) as failed:
                    imp_manifest.run_signature(self.context())
                self.assertEqual(failed.exception.code, 'IMPORT_WRONG_SYSTEM')

    def test_own_version_needs_no_compatibility_file(self):
        imp_paths.workstation().content_compatible_versions.unlink()
        self.accept(self.context(accepts=(SYSTEM,)))

    def test_invalid_release_cannot_use_compatibility_list(self):
        imp_paths.workstation().release_file.write_bytes(b'!invalid\n')
        with self.assertRaises(imp_fs.ImportFailure) as failed:
            imp_manifest.run_signature(self.context())
        self.assertEqual(failed.exception.code, 'IMPORT_WRONG_SYSTEM')

    def test_pointerless_switch_check_and_reload(self):
        ctx = self.install(self.context())
        target = ctx.desired[0]['target']
        self.assertEqual(ctx.registry.active['targets'], [target])
        self.assertEqual(ctx.journal.data['switch']['pointers'], [])
        self.assertEqual(imp_journal.Journal(ctx.p).data['desired'][0]['pointer'], None)
        self.assertEqual(imp_journal.Registry(ctx.p).active['targets'], [target])
        self.assertTrue(imp_switch.already_current(ctx))
        self.assertTrue(imp_units.verify_desired(ctx, ctx.desired[0]))

    def test_active_and_previous_pointerless_generations_are_retained(self):
        first = self.install(self.context(generation='g1'))
        old = first.desired[0]['target']
        second = self.install(self.context(generation='g2'))
        new = second.desired[0]['target']
        self.assertEqual(second.registry.previous['targets'], [old])
        self.assertTrue((second.p.cr / old).is_dir())
        self.assertTrue((second.p.cr / new).is_dir())
        self.assertEqual(imp_journal.Registry(second.p).previous['targets'], [old])
        with patch.object(imp_index, 'library_removed'):
            third = self.install(self.context(generation='g3'))
        self.assertFalse((third.p.cr / old).exists())
        self.assertTrue((third.p.cr / new).is_dir())
        self.assertTrue((third.p.cr / third.desired[0]['target']).is_dir())

    def test_missing_pointerless_generation_is_not_already_current(self):
        ctx = self.install(self.context())
        imp_fs.remove_tree(ctx.p.cr / ctx.desired[0]['target'])
        self.assertFalse(imp_switch.already_current(ctx))

    def test_pointerless_reimport_is_current(self):
        first = self.install(self.context())
        second = self.install(self.context())
        self.assertEqual(second.report.unit('lib-cisco')['state'], 'current')
        self.assertIsNone(second.registry.previous)
        self.assertEqual(second.registry.active['targets'], first.registry.active['targets'])

    def test_current_pointerless_progress_counts_only_member_validation(self):
        self.install(self.context())
        ctx = self.accept(self.context())
        expected = ctx.manifest['counts']['bytes']
        self.assertEqual(imp_files.initialise_progress(ctx), expected)
        imp_files.run_files(ctx)
        imp_files.run_space(ctx)
        imp_files.run_library(ctx)
        self.assertEqual(ctx.report.unit('lib-cisco')['state'], 'current')
        self.assertEqual(ctx.report.data['progress']['total_bytes'], expected)
        self.assertEqual(ctx.report.data['progress']['done_bytes'], expected)

    def test_new_pointerless_progress_includes_library_bytes(self):
        ctx = self.accept(self.context())
        expected = ctx.manifest['counts']['bytes'] + ctx.desired[0]['bytes']
        self.assertEqual(imp_files.initialise_progress(ctx), expected)
        imp_files.run_files(ctx)
        imp_files.run_space(ctx)
        imp_files.run_library(ctx)
        self.assertEqual(ctx.report.data['progress']['total_bytes'], expected)
        self.assertEqual(ctx.report.data['progress']['done_bytes'], expected)

    def test_index_uses_pointerless_program_records(self):
        ctx = self.install(self.context())
        cisco, ietf, layers = imp_index._sources(ctx, {})
        self.assertEqual(cisco, [ctx.p.cr / ctx.desired[0]['target']])
        self.assertIsNone(ietf)
        self.assertIsNone(layers)

    def test_library_with_derived_pointer_still_switches(self):
        ctx = self.install(self.context(pack='lib-encyclopedia'))
        target = ctx.desired[0]['target']
        self.assertEqual(imp_switch.read_pointer(ctx.p.cr / 'encyclopedia'), target)
        self.assertEqual(ctx.registry.active['pointers'], {'encyclopedia': target})
        self.assertNotIn('targets', ctx.registry.active)
        self.assertTrue(imp_switch.already_current(ctx))

    def test_pointerless_check_failure_restores_saved_active_targets(self):
        first = self.install(self.context(generation='g1'))
        second = self.accept(self.context(generation='g2'))
        imp_files.run_files(second)
        imp_files.run_space(second)
        imp_files.run_library(second)
        imp_switch.run_switch(second)
        second.report.data['kb']['embedding'] = {'model_id': 'test', 'revision': 'test', 'dimension': 1}
        os.environ['D32_TEST_FAULTS'] = 'check:lib-cisco'
        imp_paths._HOOKS = None
        with self.assertRaises(imp_fs.ImportFailure) as failed:
            imp_switch.run_check(second)
        self.assertEqual(failed.exception.code, 'IMPORT_CHECK')
        self.assertEqual(second.registry.active['targets'], first.registry.active['targets'])
        self.assertEqual(imp_journal.Registry(second.p).active['targets'], first.registry.active['targets'])


if __name__ == '__main__':
    unittest.main(verbosity=2)
