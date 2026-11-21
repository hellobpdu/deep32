#!/usr/bin/env python3
"""Offline regressions for the Workstation's import of disc 2: one read of the disc, the knowledge base first.

A synthetic disc 2 (a knowledge base, two Library packs, the Cisco titles index, a corpus block, code and a root
file, under a manifest the importer accepts) is imported by d32import.main with the disc already in place and the
child programs answered in process. Every open of a file on the disc is counted, so each test can say how often the
importer read it, and the service commands and console lines are recorded, so each test can say what was in use
when. The stage folder keeps a Library part as chunk files; the tests make those chunks small.

The knowledge base part is the knowledge base's files and the corpus block's block.json; the Library part is every
other member. The knowledge base goes into use, alone, before any member of the Library part is read.
"""
from collections import Counter
import contextlib
import hashlib
import importlib.machinery
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
IMPORTER = ROOT / 'src/workstation/distrib/site/usr/local/d32/libexec/d32-import'
SITE = ROOT / 'src/workstation/distrib/site'
sys.path.insert(0, str(IMPORTER))
import d32import
import imp_c6
import imp_files
import imp_fs
import imp_index
import imp_journal
import imp_paths
import imp_switch
import imp_units

FINGERPRINT = '0123456789ABCDEF0123456789ABCDEF01234567'
SYSTEM = '0.1.0-alpha.2'
KB_FINGERPRINT = '0123456789abcdef'
OTHER_FINGERPRINT = 'fedcba9876543210'
MANIFESTS = ('MANIFEST.json', 'MANIFEST.json.asc')
LIBRARY_POINTERS = ('encyclopedia', 'ietf', 'cisco')
READY = 'the knowledge base is ready; the Library is still importing'


def load(name, path):
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


UIJSON = load('import_report_window', SITE / 'usr/local/d32/libexec/d32-uijson')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_row(name, data):
    return {'path': name, 'size': len(data), 'sha256': digest(data)}


def library_stream(pack, generation, prefix, files):
    """A C5 pack stream: content under its install prefix, then its record MANIFEST.tsv and INFO."""
    content = [(prefix + '/' + name, data) for name, data in files]
    records = 'var/db/d32/packs/' + pack + '/'
    info = ('name=' + pack + '\nclass=library\nrelease=' + generation + '\nrights=synthetic\nfiles=' +
            str(len(content)) + '\nbytes=' + str(sum(len(d) for _, d in content)) + '\nmade=1\ngeneration=' +
            generation + '\n').encode('ascii')
    manifest = ''.join(name + '\tf\t0440\troot\t_d32users\t' + str(len(data)) + '\t' + digest(data) +
                       '\tsynthetic\n' for name, data in content + [(records + 'INFO', info)]).encode('ascii')
    out = io.BytesIO()
    with tarfile.open(fileobj=out, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for name, data in content + [(records + 'MANIFEST.tsv', manifest), (records + 'INFO', info)]:
            member = tarfile.TarInfo(name)
            member.uid = member.gid = 0
            member.uname, member.gname = 'root', '_d32users'
            member.mode, member.mtime, member.size = 0o440, 1, len(data)
            archive.addfile(member, io.BytesIO(data))
    return out.getvalue()


def cisco_index(folder, generation, fingerprint):
    path = folder / 'cisco-index.sqlite'
    with contextlib.closing(sqlite3.connect(path)) as connection:
        connection.execute('CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT)')
        connection.executemany('INSERT INTO meta VALUES (?, ?)', (('kb_fingerprint', fingerprint),
                                                                  ('kb_generation', generation)))
        connection.commit()
    data = path.read_bytes()
    path.unlink()
    return data


class _Harness(unittest.TestCase):
    """A synthetic disc 2, the importer run in process, and what each run read, ran and printed."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='import-one-pass-', dir=os.environ.get('D32_TEST_WORK'))
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.env = {
            'D32_ROOT': str(self.root), 'D32_STUBS': str(self.root / 'stubs'),
            'D32_TEST_CLOCK': '1800000000', 'D32_TEST_RAND': '0123abcd', 'D32_TEST_FREE_BYTES': '1000000000',
            'D32_TEST_POINTERS': 'linkfile', 'D32_TEST_FAULTS': '', 'D32_TEST_CRASH': '',
            'D32_TEST_SIGNAL_AT': '', 'D32_TEST_LIVE_PIDS': '',
        }
        patch = mock.patch.dict(os.environ, self.env)
        patch.start()
        self.addCleanup(patch.stop)
        # Small chunks, and the journal records progress after every member. create=True lets the same scenarios
        # run against an importer without these settings, to show what changed.
        for module, name, value in ((imp_paths, '_HOOKS', None), (imp_fs, '_SIGNALLED', False),
                                    (imp_fs, '_SIGNALS_IGNORED', False), (imp_files, 'STAGE_CHUNK', 2048),
                                    (imp_files, 'SAVE_BYTES', 1)):
            patch = mock.patch.object(module, name, value, create=True)
            patch.start()
            self.addCleanup(patch.stop)
        imp_fs._USED_WRITE_FAULTS.clear()
        self.addCleanup(imp_fs._USED_WRITE_FAULTS.clear)
        self.paths = p = imp_paths.workstation()
        for folder in (p.cr, p.release_file.parent, p.release_key.parent, p.services_file.parent, self.root / 'stubs'):
            folder.mkdir(parents=True, exist_ok=True)
        p.release_file.write_text(SYSTEM + '\n', encoding='ascii')
        p.release_key.write_bytes(b'SYNTHETIC PUBLIC KEY FIXTURE\n')
        p.release_fingerprint.write_text(FINGERPRINT + '\n', encoding='ascii')
        # The engine and the encyclopedia server are enabled, so the importer restarts them when it switches.
        p.services_file.write_text('pkg_scripts=d32core d32edge kiwix_serve\n', encoding='ascii')
        (self.root / 'stubs/gpgv.py').write_bytes(b'# SYNTHETIC VERIFIER FIXTURE\n')
        self.make_disc()
        self.children_argv, self.messages, self.commands, self.events = [], [], [], []

    # ---------------------------------------------------------------------------------------------- the disc

    def make_disc(self, kb_name='test-kb', kb_fingerprint=KB_FINGERPRINT, generation='g1', salt=b''):
        """Write a disc 2 into the mount folder. Another name, fingerprint, generation and salt make a newer disc."""
        if self.paths.mount.exists():
            shutil.rmtree(self.paths.mount)
        self.kb_name, self.kb_fingerprint, self.generation = kb_name, kb_fingerprint, generation
        bundle = (json.dumps({'generation': kb_name, 'fingerprint': kb_fingerprint,
                              'engine': {'min': '1.0.0', 'max': '1.0.0'},
                              'embedding': {'model_id': 'synthetic', 'revision': 'r1', 'dimension': 4}},
                             indent=2) + '\n').encode('ascii')
        kb_manifest = b'{"synthetic": true}\n' + salt
        base = 'kb/' + kb_name + '/'
        self.kb = {base + 'BUNDLE.json': bundle, base + 'MANIFEST.json': kb_manifest,
                   base + 'dense/points.bin': bytes(range(256)) * 20, base + 'root/doc-1.txt': b'doc one\n' + salt}
        self.content = {
            'lib-encyclopedia': ('.encyclopedia-' + generation,
                                 [('a/article-1.txt', b'one\n' * 40), ('a/article-2.txt', b'two\n' + salt),
                                  ('b/article-3.txt', b'three\n' * 9), ('c/article-4.txt', b'4\n'),
                                  ('manifest.jsonl', b'')]),
            'lib-ietf': ('.ietf-' + generation, [('rfc1.txt', b'rfc one\n' * 30), ('rfc2.txt', b'rfc two\n' + salt)]),
        }
        payloads = dict(self.kb)
        payloads.update({'LIBRARY-CREDITS.tsv': b'credits\n', 'code/README.txt': b'code\n',
                         'corpus/rfc/block.json': b'{"block": "rfc"}\n', 'corpus/rfc/rfc-1.tar': b'R' * 3000})
        self.cisco = cisco_index(self.root, kb_name, kb_fingerprint)
        payloads['library/cisco-public.sqlite'] = self.cisco
        library = []
        for pack, (target, files) in self.content.items():
            stream = library_stream(pack, generation, 'd32/libcontent/' + target, files)
            part_size = 6144 if pack == 'lib-encyclopedia' else len(stream)
            parts = []
            for index, offset in enumerate(range(0, len(stream), part_size), 1):
                path = 'library/%s/%s-%s.tar.part%03d' % (pack, pack, generation, index)
                payloads[path] = stream[offset:offset + part_size]
                parts.append({'index': index, **file_row(path, payloads[path])})
            library.append({'pack': pack, 'generation': generation, 'release': generation, 'program_pack': None,
                            'pointer': None, 'install_prefix': 'd32/libcontent/' + target, 'kb_generation': None,
                            'kb_fingerprint': None, 'has_cite_map': False, 'stream_bytes': len(stream),
                            'stream_sha256': digest(stream), 'part_size': part_size, 'parts': parts})
        self.encyclopedia_parts = [part['path'] for part in library[0]['parts']]
        self.assertEqual(len(self.encyclopedia_parts), 2)
        members = sorted(payloads, key=lambda name: name.encode('utf-8'))
        manifest = {
            'format': 'deep32-content-manifest', 'format_version': 1,
            'disc': {'role': 'content', 'version': SYSTEM, 'volume_id': 'TEST_CONTENT', 'label': 'TEST',
                     'created': '2000-01-01T00:00:00Z', 'build_option': 'standard'},
            'accepts_system': [SYSTEM], 'signer': {'type': 'openpgp', 'fingerprint': FINGERPRINT},
            'kb': {'path': 'kb/' + kb_name, 'generation': kb_name, 'fingerprint': kb_fingerprint,
                   'created': '2000-01-01T00:00:00Z', 'bundle_json_sha256': digest(bundle),
                   'manifest_sha256': digest(kb_manifest), 'documents': 1, 'chunks': 1, 'points': 1,
                   'by_collection': {}},
            'blocks': [{'name': 'rfc', 'version': '1.0.0', 'path': 'corpus/rfc/block.json',
                        'block_json_sha256': digest(payloads['corpus/rfc/block.json']), 'documents': 1,
                        'archive_files': [file_row('corpus/rfc/rfc-1.tar', payloads['corpus/rfc/rfc-1.tar'])]}],
            'library': library,
            'cisco_public': {**file_row('library/cisco-public.sqlite', self.cisco), 'catalogue_generation': 'c1',
                             'scope': 'public', 'kb_generation': kb_name, 'kb_fingerprint': kb_fingerprint,
                             'program_path': 'cisco'},
            'code': {'path': 'code', 'files': 1, 'bytes': len(payloads['code/README.txt'])},
            'root_files': [file_row('LIBRARY-CREDITS.tsv', payloads['LIBRARY-CREDITS.tsv'])],
            'members': [file_row(name, payloads[name]) for name in members],
            'counts': {'files': len(members), 'bytes': sum(map(len, payloads.values()))},
        }
        self.members, self.payloads = members, payloads
        self.kb_part = [name for name in members if name.startswith('kb/') or name == 'corpus/rfc/block.json']
        self.library_part = [name for name in members if name not in self.kb_part]
        self.manifest_bytes = (json.dumps(manifest, indent=2) + '\n').encode('ascii')
        self.disc = self.paths.mount
        for name, data in payloads.items():
            path = self.disc.joinpath(*name.split('/'))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        (self.disc / 'MANIFEST.json').write_bytes(self.manifest_bytes)
        (self.disc / 'MANIFEST.json.asc').write_bytes(b'SYNTHETIC SIGNATURE\n')

    # ---------------------------------------------------------------------------------------------- running it

    def children(self, ctx):
        def command(name, args, timeout=600):
            self.commands.append((name,) + tuple(args))
            self.events.append(('command', name) + tuple(args))
            text = 'label: SYNTHETIC\n  c: 100 0 ISO9660\n' if name == 'disklabel' else ''
            return subprocess.CompletedProcess([name] + list(args), 0, text, '')

        def run_child(argv, timeout=600):
            argv = [str(x) for x in argv]
            self.children_argv.append(argv)
            for value in argv:
                if value.startswith('msg='):
                    self.messages.append(value[4:])
                    self.events.append(('c6', argv[argv.index('set') + 3], value[4:]))
            if any(x.endswith('kb_bundle.py') for x in argv) and 'info' in argv:
                return subprocess.CompletedProcess(argv, 0, 'kb_generation=' + self.kb_name + '\nkb_fingerprint=' +
                                                   self.kb_fingerprint + '\n', '')
            return subprocess.CompletedProcess(argv, 0, '', '')
        ctx.command, ctx.run_child = command, run_child

    def run_import(self, *argv, **env):
        """One run of the importer; returns its exit status and how often it opened each file on the disc."""
        os.environ.update({'D32_TEST_FAULTS': '', 'D32_TEST_SIGNAL_AT': '', 'D32_TEST_FREE_BYTES': '1000000000'}, **env)
        imp_paths._HOOKS = None
        imp_fs._SIGNALLED = imp_fs._SIGNALS_IGNORED = False
        reads = Counter()
        disc = os.path.abspath(self.disc)
        prefix = os.path.normcase(disc) + os.sep
        real_open = os.open

        def counted(path, flags, *args, **kwargs):
            full = os.path.abspath(os.fspath(path))
            if os.path.normcase(full).startswith(prefix):
                name = full[len(disc) + 1:].replace(os.sep, '/')
                reads[name] += 1
                self.events.append(('read', name))
            return real_open(path, flags, *args, **kwargs)
        with mock.patch.object(d32import, '_children', self.children), \
                mock.patch.object(imp_fs, 'install_signal_handlers'), \
                mock.patch.object(imp_index, 'regenerate'), \
                mock.patch.object(imp_index, 'library_removed'), \
                mock.patch.object(os, 'open', counted):
            result = d32import.main(['--node', 'workstation'] + list(argv or ('--device', 'cd0')))
        imp_fs._SIGNALLED = False
        return result, reads

    def report(self):
        raw = self.paths.report.read_bytes()
        self.assertEqual(UIJSON.check(raw), [])
        return json.loads(raw.decode('ascii'))

    def journal(self):
        return json.loads(self.paths.state_file.read_text(encoding='ascii'))

    def stage_folders(self):
        return sorted(path.name for path in self.paths.cr.glob('.import-stage-*'))

    def pointers(self):
        return {name: imp_switch.read_pointer(self.paths.cr / name) for name in ('kb',) + LIBRARY_POINTERS}

    def kb_value(self):
        return '.kb-' + self.kb_name + '/' + self.kb_name

    def library_values(self):
        return {'encyclopedia': '.encyclopedia-' + self.generation, 'ietf': '.ietf-' + self.generation,
                'cisco': '.cisco-' + digest(self.cisco)[:16]}

    def assert_installed(self):
        report = self.report()
        self.assertEqual(report['outcome'], 'imported')
        cr = self.paths.cr
        registry = imp_journal.Registry(self.paths)
        self.assertEqual(registry.active['pointers'], {'kb': self.kb_value(), **self.library_values()})
        self.assertNotIn('library_pending', registry.active)
        self.assertEqual(self.pointers(), registry.active['pointers'])
        folder = cr / ('.kb-' + self.kb_name)
        for name, data in self.kb.items():
            self.assertEqual(folder.joinpath(*name.split('/')[1:]).read_bytes(), data)
        self.assertEqual(sorted('kb/' + p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()),
                         sorted(self.kb))
        for pack, (target, files) in self.content.items():
            for name, data in files:
                self.assertEqual((cr / target).joinpath(*name.split('/')).read_bytes(), data)
            self.assertEqual(len([p for p in (cr / target).rglob('*') if p.is_file()]), len(files))
        self.assertEqual((cr / self.library_values()['cisco'] / 'cisco-public.sqlite').read_bytes(), self.cisco)
        self.assertEqual(self.stage_folders(), [])
        return report

    def assert_not_switched(self):
        registry = imp_journal.Registry(self.paths)
        self.assertIsNone(registry.active)
        for pointer in ('kb',) + LIBRARY_POINTERS:
            self.assertFalse(os.path.lexists(self.paths.cr / pointer), pointer)

    def assert_kb_only(self, library=None):
        """This disc's knowledge base is in use and its Library is not: the Library in use is `library` (pointers of
        an older import), or none."""
        library = library or {}
        registry = imp_journal.Registry(self.paths)
        self.assertEqual(registry.active['pointers'], {'kb': self.kb_value(), **library})
        self.assertTrue(registry.active['library_pending'])
        self.assertEqual(self.pointers(), {'kb': self.kb_value(), **{name: library.get(name)
                                                                     for name in LIBRARY_POINTERS}})
        return registry

    def assert_failed_with_kb_in_use(self, errors, resumable=False):
        report = self.report()
        self.assertEqual((report['state'], report['outcome']), ('failed', 'failed'))
        self.assertEqual([(e['code'], e['stage'], e['unit'], e['path']) for e in report['errors']], errors)
        self.assertEqual([w['code'] for w in report['warnings'] if w['code'] == 'IMPORT_KB_ONLY'], ['IMPORT_KB_ONLY'])
        self.assertEqual(report['resume']['resumable'], resumable)
        stages = {row['id']: row['state'] for row in report['stages']}
        self.assertEqual([stages[name] for name in ('files', 'space', 'kb')], ['done', 'done', 'done'])
        self.assertEqual(report['units'][0]['state'], 'installed')
        self.assertEqual(report['kb']['engine_ok'], True)
        self.assertEqual(self.messages[-1], 'the knowledge base is ready; the import of the Library stopped: '
                                            'see Import Report')
        self.assertEqual(self.journal()['outcome'], 'failed')
        return report

    def staged(self):
        return sorted(p.relative_to(self.paths.cr).as_posix() for p in self.paths.cr.glob('.import-stage-*/**/*')
                      if p.is_file())

    def first_library_read(self):
        return next(index for index, event in enumerate(self.events)
                    if event[0] == 'read' and event[1] in self.library_part)

    def engine_commands(self):
        return [(index, event[2:]) for index, event in enumerate(self.events)
                if event[:2] == ('command', 'rcctl') and event[3] in ('d32core', 'd32edge')]


class SinglePassImport(_Harness):
    # ---------------------------------------------------------------------------------------------- the order

    def test_happy_path_reads_each_disc_file_once(self):
        extracted = []
        real_extract = imp_units._extract_stream

        def extract(ctx, *args):
            extracted.append((ctx.journal.data['stages']['files']['state'], ctx.journal.data['stages']['kb']['state'],
                              'library_verified' in ctx.journal.data))
            return real_extract(ctx, *args)
        with mock.patch.object(imp_units, '_extract_stream', extract):
            result, reads = self.run_import()
        self.assertEqual(result, 0)
        self.assertEqual(reads, Counter({name: 1 for name in self.members + list(MANIFESTS)}))
        report = self.assert_installed()
        stages = {row['id']: row for row in report['stages']}
        self.assertEqual(stages['files']['done_bytes'], sum(len(self.payloads[name]) for name in self.kb_part))
        self.assertEqual((stages['files']['done_items'], stages['files']['total_items']), (5, 5))
        # The Library stage counts the Library part's members and the bytes it read and unpacked.
        self.assertEqual((stages['library']['done_items'], stages['library']['total_items']), (7, 7))
        self.assertEqual(stages['library']['done_bytes'], stages['library']['total_bytes'])
        self.assertEqual(report['progress']['done_bytes'], report['progress']['total_bytes'])
        # Unpacking starts only after the knowledge base part is done and the Library part has checked its members.
        self.assertEqual(extracted, [('done', 'done', True)] * 2)
        # kb_bundle checks the knowledge base against the checked copy of block.json, never the disc's.
        blocks = [argv[argv.index('--block') + 1] for argv in self.children_argv if '--block' in argv]
        self.assertTrue(blocks)
        for path in blocks:
            self.assertIn('.import-stage-0123abcd', path)
            self.assertNotIn(os.path.abspath(self.disc), os.path.abspath(path))
        self.assertIn('copying and checking the knowledge base files of disc 2', self.messages)
        self.assertIn(READY, self.messages)
        self.assertEqual(self.messages[-2:], ['disc 2 0.1.0-alpha.2 imported', 'ready'])
        self.assertTrue([m for m in self.messages if re.fullmatch(
            r'the knowledge base is ready; Library [0-9]+ %: [0-9] of 7 files checked \(0 of 1 MB\), [0-3] of 3 '
            r'packs installed(, about [0-9]+ min left)?', m)])

    def test_usb_stick_with_the_disc_2_image_imports_like_the_disc(self):
        result, reads = self.run_import('--device', 'sd1')
        self.assertEqual(result, 0)
        self.assertEqual(reads, Counter({name: 1 for name in self.members + list(MANIFESTS)}))
        report = self.assert_installed()
        self.assertEqual(report['source']['device'], 'sd1')
        self.assertEqual(self.journal()['device'], 'sd1')
        # The stick's whole disk, read-only, as ISO 9660; nothing else is mounted.
        self.assertEqual([command for command in self.commands if command[0] == 'mount'],
                         [('mount', '-t', 'cd9660', '-o', 'ro', '/dev/sd1c', str(self.paths.mount))])
        # The Import Report window takes an optical drive or a USB stick, and nothing else.
        for device, good in (('cd0', True), ('cd12', True), ('sd0', True), ('sd12', True), ('sr0', True),
                             ('sd123', False), ('sd1c', False), ('sd', False), ('wd0', False), ('rsd1', False)):
            with self.subTest(device=device):
                report['source']['device'] = device
                problems = UIJSON.check((json.dumps(report, indent=2) + '\n').encode('ascii'))
                if good:
                    self.assertEqual(problems, [])
                else:
                    self.assertTrue(any('source.device' in problem for problem in problems), problems)

    def test_knowledge_base_is_in_use_before_any_library_member_is_read(self):
        seen = []
        real_member = imp_files._open_member

        def member(root, path):
            if path in self.library_part and not seen:
                registry = imp_journal.Registry(self.paths)
                seen.append((self.pointers(), registry.active, self.journal(), list(self.messages),
                             json.loads(self.paths.report.read_text(encoding='ascii'))))
            return real_member(root, path)
        with mock.patch.object(imp_files, '_open_member', member):
            result, _ = self.run_import()
        self.assertEqual(result, 0)
        self.assert_installed()
        pointers, active, journal, messages, report = seen[0]
        self.assertEqual(pointers, {'kb': self.kb_value(), 'encyclopedia': None, 'ietf': None, 'cisco': None})
        self.assertEqual(active['pointers'], {'kb': self.kb_value()})
        self.assertTrue(active['library_pending'])
        self.assertEqual(journal['kb_live']['manifest_sha256'], journal['manifest_sha256'])
        self.assertIsNone(journal['switch'])
        self.assertEqual(messages[-2:], [READY, 'the knowledge base is ready; copying, checking and installing the '
                                                'Library'])
        self.assertEqual(UIJSON.check(json.dumps(report, indent=2).encode('ascii') + b'\n'), [])
        stages = {row['id']: row['state'] for row in report['stages']}
        self.assertEqual([stages[name] for name in ('files', 'space', 'kb', 'library')],
                         ['done', 'done', 'done', 'running'])
        self.assertEqual((report['kb']['engine_ok'], report['kb']['meaning_search']), (True, 'waiting-forge'))
        # The engine went onto the new knowledge base before the first Library member was read, and only then.
        first = self.first_library_read()
        self.assertEqual([command for index, command in self.engine_commands()],
                         [('restart', 'd32core'), ('check', 'd32core')])
        self.assertTrue(all(index < first for index, _ in self.engine_commands()))
        # The second disc of the same release is already in use: nothing is switched or restarted.
        self.events.clear()
        self.assertEqual(self.run_import()[0], 0)
        self.assertEqual(self.report()['outcome'], 'already-current')
        self.assertEqual(self.engine_commands(), [])

    def test_unpacking_removes_each_chunk_once_it_is_read(self):
        asked = []
        real_join = imp_files._ChunkRoot.joinpath

        def join(chunks, *parts):
            path = real_join(chunks, *parts)
            index = chunks.order['/'.join(parts)]
            asked.append((index, [os.path.lexists(chunks.root.joinpath(*name.split('/')))
                                  for name in chunks.names[:index]], path.is_file()))
            return path
        with mock.patch.object(imp_files._ChunkRoot, 'joinpath', join):
            result, _ = self.run_import()
        self.assertEqual(result, 0)
        self.assert_installed()
        # 6144 + 4096 bytes of lib-encyclopedia and 10240 of lib-ietf, in chunks of 2048 bytes.
        self.assertEqual(len(asked), 3 + 2 + 5)
        for index, earlier, present in asked:
            self.assertTrue(present)
            self.assertEqual(earlier, [False] * index)

    # ---------------------------------------------------------------------------------------------- refusals

    def test_tampered_knowledge_base_member_stops_before_anything_is_used(self):
        tampered = 'kb/test-kb/dense/points.bin'
        path = self.disc.joinpath(*tampered.split('/'))
        data = bytearray(path.read_bytes())
        data[100] ^= 0xFF
        path.write_bytes(bytes(data))
        result, reads = self.run_import()
        self.assertEqual(result, 2)
        report = self.report()
        self.assertEqual([(e['code'], e['stage'], e['path']) for e in report['errors']],
                         [('IMPORT_FILE_HASH', 'files', tampered)])
        self.assertEqual(report['warnings'], [])
        self.assertFalse(report['resume']['resumable'])
        later = self.kb_part[self.kb_part.index(tampered) + 1:] + self.library_part
        self.assertEqual([name for name in later if reads[name]], [])
        self.assert_not_switched()
        self.assertEqual(self.engine_commands(), [])
        self.assertEqual(sorted(p.name for p in self.paths.cr.iterdir()), [])
        self.assertEqual(self.messages[-1], 'the import of disc 2 stopped: see Import Report')

    def test_tampered_library_part_keeps_the_new_knowledge_base_in_use(self):
        tampered = self.encyclopedia_parts[1]
        path = self.disc.joinpath(*tampered.split('/'))
        data = bytearray(path.read_bytes())
        data[100] ^= 0xFF
        path.write_bytes(bytes(data))
        with mock.patch.object(imp_units, '_extract_stream') as extract:
            result, reads = self.run_import()
        self.assertEqual(result, 2)
        self.assert_failed_with_kb_in_use([('IMPORT_FILE_HASH', 'library', 'lib-encyclopedia', tampered)])
        # The Library part stopped there: the members after it were never read, nothing was unpacked or switched.
        later = self.members[self.members.index(tampered) + 1:]
        self.assertTrue(later)
        self.assertEqual([name for name in later if reads[name]], [])
        extract.assert_not_called()
        self.assert_kb_only()
        self.assertEqual(self.stage_folders(), [])
        self.assertEqual(sorted(p.name for p in self.paths.cr.iterdir()), ['.kb-test-kb', 'kb'])
        # The engine was restarted once, onto the new knowledge base, and never after.
        self.assertTrue(all(index < self.first_library_read() for index, _ in self.engine_commands()))

    def test_copy_tampered_after_its_check_is_refused_while_unpacking(self):
        real_unpack = imp_files._unpack

        def unpack(ctx, root, desired, entry, *args):
            if entry['pack'] == 'lib-encyclopedia':
                chunk = ctx.copy_root.joinpath(*ctx.staged_chunks[self.encyclopedia_parts[0]][1]['path'].split('/'))
                data = bytearray(chunk.read_bytes())
                data[515] ^= 0xFF  # stream byte 2563: the data of b/article-3.txt, not a ustar header
                chunk.write_bytes(bytes(data))
            return real_unpack(ctx, root, desired, entry, *args)
        with mock.patch.object(imp_files, '_unpack', unpack):
            result, _ = self.run_import()
        self.assertEqual(result, 2)
        self.assert_failed_with_kb_in_use([('IMPORT_FILE_HASH', 'library', 'lib-encyclopedia',
                                            self.encyclopedia_parts[0])])
        self.assert_kb_only()
        self.assertEqual(self.stage_folders(), [])
        self.assertEqual(sorted(p.name for p in self.paths.cr.iterdir()), ['.kb-test-kb', 'kb'])

    def test_short_read_stops_and_removes_the_partial_copy(self):
        short = 'kb/test-kb/dense/points.bin'
        real_member = imp_files._open_member
        seen = []

        def member(root, path):
            handle = real_member(root, path)
            if path != short:
                return handle
            seen.append(path)
            return _ShortReads(handle, len(self.payloads[short]) - 10)
        with mock.patch.object(imp_files, '_open_member', member):
            result, reads = self.run_import()
        self.assertEqual(seen, [short])
        self.assertEqual(result, 2)
        report = self.report()
        self.assertEqual([(e['code'], e['stage'], e['path'], e['args']) for e in report['errors']],
                         [('IMPORT_FILE_SIZE', 'files', short, {'expected': 5120, 'found': 5110})])
        later = self.members[self.members.index(short) + 1:]
        self.assertEqual([name for name in later if reads[name]], [])
        self.assert_not_switched()
        self.assertEqual(self.stage_folders(), [])

    def test_read_error_in_the_library_keeps_checked_copies_and_resume_reads_the_rest_once(self):
        failing = self.encyclopedia_parts[1]
        result, first = self.run_import(D32_TEST_FAULTS='read:' + failing)
        self.assertEqual(result, 2)
        self.assert_failed_with_kb_in_use([('IMPORT_READ', 'library', 'lib-encyclopedia', failing)], resumable=True)
        self.assert_kb_only()
        staged = self.staged()
        # The partly written part is gone; what was checked before it stays for Try Again.
        self.assertFalse([name for name in staged if failing in name])
        self.assertIn('.import-stage-0123abcd/disc-copy/library/cisco-public.sqlite', staged)
        self.assertTrue([name for name in staged if self.encyclopedia_parts[0] in name])
        self.events.clear()
        result, second = self.run_import('--resume')
        self.assertEqual(result, 0)
        self.assert_installed()
        reads = first + second
        for name in self.members:
            self.assertEqual(reads[name], 2 if name == failing else 1, name)
        self.assertEqual(self.report()['attempt'], 2)
        # Try Again carried on with the Library: the engine was not touched again.
        self.assertEqual(self.engine_commands(), [])

    # ---------------------------------------------------------------------------------------------- stops

    def interrupted_then_resumed(self, point, read_twice=(), kb_in_use=False):
        result, first = self.run_import(D32_TEST_SIGNAL_AT=point)
        self.assertEqual(result, 4, point)
        if kb_in_use:
            self.assert_kb_only()
            self.assertIn(READY, self.messages)
        else:
            self.assert_not_switched()
        self.events.clear()
        result, second = self.run_import('--resume')
        self.assertEqual(result, 0, point)
        report = self.assert_installed()
        self.assertEqual(report['attempt'], 2)
        reads = first + second
        for name in self.members:
            self.assertLessEqual(reads[name], 2 if name in read_twice else 1, (point, name))
            self.assertGreaterEqual(reads[name], 1, (point, name))
        if kb_in_use:
            # The knowledge base part is not done again, and the engine is not restarted.
            self.assertEqual([name for name in self.kb_part if second[name]], [])
            self.assertEqual(self.engine_commands(), [])
        return first, second

    def test_resume_after_stop_at_free_space(self):
        self.interrupted_then_resumed('CP-space-cleaned')

    def test_resume_after_stop_halfway_through_the_copy(self):
        first, second = self.interrupted_then_resumed('CP-files-mid')
        self.assertTrue(first['kb/test-kb/BUNDLE.json'])
        self.assertFalse(second['kb/test-kb/BUNDLE.json'])

    def test_resume_after_stop_once_every_knowledge_base_member_is_checked(self):
        first, second = self.interrupted_then_resumed('CP-files-done')
        self.assertEqual([name for name in self.members if first[name]], self.kb_part)
        self.assertEqual([name for name in self.members if second[name]], self.library_part)

    def test_resume_after_stop_with_the_knowledge_base_moved_but_not_placed(self):
        _, second = self.interrupted_then_resumed('CP-kb-staged')
        self.assertEqual([name for name in self.members if second[name]], self.library_part)

    def test_resume_after_stop_with_the_knowledge_base_placed(self):
        _, second = self.interrupted_then_resumed('CP-kb-placed')
        self.assertEqual([name for name in self.members if second[name]], self.library_part)

    def test_resume_after_stop_during_the_knowledge_base_check_undoes_its_switch_first(self):
        result, _ = self.run_import(D32_TEST_SIGNAL_AT='CP-kb-check')
        self.assertEqual(result, 4)
        # Stopped with the kb pointer moved and the check not done.
        self.assertEqual(self.pointers()['kb'], self.kb_value())
        self.assertEqual(self.journal()['switch']['part'], 'kb')
        self.assertEqual([command for _, command in self.engine_commands()],
                         [('restart', 'd32core'), ('check', 'd32core')])
        undone = []
        real_identity = d32import._identity

        def identity(ctx, *args):
            # The switch is undone before the disc is read: nothing is in use, the engine was restarted again.
            undone.append((self.pointers()['kb'], imp_journal.Registry(self.paths).active, ctx.journal.data['switch']))
            return real_identity(ctx, *args)
        self.events.clear()
        with mock.patch.object(d32import, '_identity', identity):
            result, second = self.run_import('--resume')
        self.assertEqual(result, 0)
        self.assertEqual(undone[0], (None, None, None))
        self.assert_installed()
        self.assertIn('knowledge base switch undone after a stop', self.journal()['notes'])
        # Restored once (the undo), then switched and checked again.
        self.assertEqual([command for _, command in self.engine_commands()],
                         [('restart', 'd32core'), ('check', 'd32core'), ('restart', 'd32core'), ('check', 'd32core')])
        self.assertEqual([name for name in self.members if second[name]], self.library_part)

    def test_pointer_failure_in_the_knowledge_base_switch_changes_nothing_and_try_again_finishes(self):
        result, _ = self.run_import(D32_TEST_FAULTS='pointer:kb')
        self.assertEqual(result, 2)
        report = self.report()
        self.assertEqual([(e['code'], e['stage'], e['unit']) for e in report['errors']], [('IMPORT_SWITCH', 'kb', 'kb')])
        self.assertTrue(report['resume']['resumable'])
        self.assert_not_switched()
        self.assertEqual(self.engine_commands(), [])
        self.assertEqual(self.run_import('--resume')[0], 0)
        self.assert_installed()

    def test_error_after_the_knowledge_base_switch_puts_the_previous_one_back_at_once(self):
        # An unexpected error in the check fails the import resumably; the unchecked knowledge base is not left in use.
        with mock.patch.object(imp_switch, '_kb_check', side_effect=RuntimeError('synthetic')):
            result, _ = self.run_import()
        self.assertEqual(result, 2)
        report = self.report()
        self.assertEqual([e['code'] for e in report['errors']], ['IMPORT_INTERNAL'])
        self.assertTrue(report['resume']['resumable'])
        journal = self.journal()
        self.assertEqual((journal['outcome'], journal['switch']), ('failed', None))
        self.assertIn('knowledge base switch undone after a stop', journal['notes'])
        self.assert_not_switched()
        self.assertEqual([command for _, command in self.engine_commands()],
                         [('restart', 'd32core'), ('check', 'd32core'), ('restart', 'd32core'), ('check', 'd32core')])
        self.events.clear()
        result, _ = self.run_import('--resume')
        self.assertEqual(result, 0)
        self.assert_installed()
        self.assertEqual([command for _, command in self.engine_commands()], [('restart', 'd32core'), ('check', 'd32core')])

    def test_resume_after_stop_once_the_knowledge_base_is_in_use(self):
        _, second = self.interrupted_then_resumed('CP-kb-live', kb_in_use=True)
        self.assertEqual([name for name in self.members if second[name]], self.library_part)

    def test_resume_after_stop_halfway_through_the_library_copy(self):
        first, second = self.interrupted_then_resumed('CP-library-copy-mid', kb_in_use=True)
        self.assertTrue(first['LIBRARY-CREDITS.tsv'])
        self.assertFalse(second['LIBRARY-CREDITS.tsv'])

    def test_resume_after_stop_once_every_library_member_is_checked(self):
        _, second = self.interrupted_then_resumed('CP-library-copied', kb_in_use=True)
        self.assertEqual([name for name in self.members if second[name]], [])

    def test_resume_after_stop_halfway_through_unpacking(self):
        # Unpacking had removed chunks it had read, so only those parts come from the disc again.
        _, second = self.interrupted_then_resumed('CP-library-mid', read_twice=self.encyclopedia_parts,
                                                  kb_in_use=True)
        self.assertEqual([name for name in self.members if second[name] and name not in self.encyclopedia_parts], [])

    def test_resume_after_stop_with_a_library_group_placed(self):
        _, second = self.interrupted_then_resumed('CP-library-group-placed', kb_in_use=True)
        self.assertEqual([name for name in self.members if second[name]], [])

    def test_resume_after_stop_with_the_cisco_index_placed(self):
        _, second = self.interrupted_then_resumed('CP-cisco-placed', kb_in_use=True)
        self.assertEqual([name for name in self.members if second[name]], [])

    def test_resume_hashes_staged_knowledge_base_copies_and_reads_only_damaged_ones_again(self):
        result, first = self.run_import(D32_TEST_SIGNAL_AT='CP-files-done')
        self.assertEqual(result, 4)
        copies = next(self.paths.cr.glob('.import-stage-*')) / 'disc-copy'
        damaged = copies / 'kb/test-kb/root/doc-1.txt'
        damaged.write_bytes(b'DOC ONE\n')
        (copies / 'kb/test-kb/extra.txt').write_bytes(b'not on the disc\n')
        stray = next(self.paths.cr.glob('.import-stage-*')) / '.kb-test-kb'
        stray.mkdir()
        (stray / 'stray.txt').write_bytes(b'left by an earlier attempt\n')
        result, second = self.run_import('--resume')
        self.assertEqual(result, 0)
        self.assert_installed()
        self.assertEqual({name for name in self.kb_part if second[name]}, {'kb/test-kb/root/doc-1.txt'})
        self.assertFalse((self.paths.cr / '.kb-test-kb/test-kb/extra.txt').exists())
        self.assertFalse((self.paths.cr / '.kb-test-kb/stray.txt').exists())

    def test_resume_hashes_staged_library_copies_and_reads_only_damaged_ones_again(self):
        result, first = self.run_import(D32_TEST_SIGNAL_AT='CP-library-copied')
        self.assertEqual(result, 4)
        copies = next(self.paths.cr.glob('.import-stage-*')) / 'disc-copy'
        chunk = copies / 'library/lib-ietf/lib-ietf-g1.tar.part001.c0003'
        chunk.write_bytes(chunk.read_bytes()[:-1])
        (copies / 'library/extra.txt').write_bytes(b'not on the disc\n')
        stray = next(self.paths.cr.glob('.import-stage-*')) / '.encyclopedia-g1'
        stray.mkdir()
        (stray / 'stray.txt').write_bytes(b'left by an earlier attempt\n')
        result, second = self.run_import('--resume')
        self.assertEqual(result, 0)
        self.assert_installed()
        self.assertEqual({name for name in self.members if second[name]}, {'library/lib-ietf/lib-ietf-g1.tar.part001'})
        self.assertFalse((self.paths.cr / '.encyclopedia-g1/stray.txt').exists())

    # ---------------------------------------------------------------------------------------------- free space

    def test_free_space_is_refused_before_any_member_is_read(self):
        result, reads = self.run_import(D32_TEST_FREE_BYTES='4096')
        self.assertEqual(result, 2)
        report = self.report()
        total = sum(len(self.payloads[name]) for name in self.kb_part)
        self.assertEqual([(e['code'], e['stage'], e['args']) for e in report['errors']],
                         [('IMPORT_SPACE', 'files', {'needed_bytes': (total * 105 + 99) // 100, 'free_bytes': 4096})])
        self.assertTrue(report['resume']['resumable'])
        self.assertEqual(reads, Counter({name: 1 for name in MANIFESTS}))
        self.assertEqual(self.stage_folders(), [])
        self.assert_not_switched()

    def test_free_space_for_the_library_is_refused_before_any_library_member_is_read(self):
        kb = sum(len(self.payloads[name]) for name in self.kb_part)
        staged = sum(len(self.payloads[name]) for name in self.library_part if name.startswith('library/'))
        free = (staged * 105 + 99) // 100 - 1
        self.assertGreater(free, (kb * 105 + 99) // 100)
        result, first = self.run_import(D32_TEST_FREE_BYTES=str(free))
        self.assertEqual(result, 2)
        report = self.assert_failed_with_kb_in_use([('IMPORT_SPACE', 'library', None, None)], resumable=True)
        self.assertEqual(report['errors'][0]['args'], {'needed_bytes': free + 1, 'free_bytes': free})
        self.assertEqual([name for name in self.library_part if first[name]], [])
        self.assert_kb_only()
        result, second = self.run_import('--resume')
        self.assertEqual(result, 0)
        self.assert_installed()
        self.assertEqual([name for name in self.members if second[name]], self.library_part)

    def test_space_stage_refuses_when_the_unpacking_margin_is_gone(self):
        real = imp_files.free_bytes

        def free(path):
            files = json.loads(self.paths.state_file.read_text(encoding='ascii'))['stages']['files']['state']
            return real(path) if files == 'running' else 10
        with mock.patch.object(imp_files, 'free_bytes', free):
            result, _ = self.run_import()
        self.assertEqual(result, 2)
        report = self.report()
        self.assertEqual([(e['code'], e['stage']) for e in report['errors']], [('IMPORT_SPACE', 'space')])
        self.assertTrue(report['resume']['resumable'])
        self.assert_not_switched()
        result, _ = self.run_import('--resume')
        self.assertEqual(result, 0)
        self.assert_installed()

    def test_disc_already_in_use_is_read_once_and_nothing_is_staged(self):
        self.assertEqual(self.run_import()[0], 0)
        staged = []
        real_reserve = imp_files._reserve
        with mock.patch.object(imp_files, '_reserve', side_effect=lambda *a: staged.append(a) or real_reserve(*a)):
            result, reads = self.run_import()
        self.assertEqual(result, 0)
        self.assertEqual(self.report()['outcome'], 'already-current')
        self.assertEqual(reads, Counter({name: 1 for name in self.members + list(MANIFESTS)}))
        self.assertEqual(staged, [])
        self.assertEqual(self.stage_folders(), [])
        self.assertIn('checking every file of disc 2', self.messages)

    # ---------------------------------------------------------------------------------------------- checks

    def test_knowledge_base_check_failure_puts_the_previous_one_back(self):
        result, reads = self.run_import(D32_TEST_FAULTS='check:kb')
        self.assertEqual(result, 2)
        report = self.report()
        self.assertEqual(report['outcome'], 'rolled-back')
        self.assertEqual([(e['code'], e['stage'], e['args']) for e in report['errors']],
                         [('IMPORT_CHECK', 'kb', {'what': 'engine'})])
        self.assertEqual([w['code'] for w in report['warnings'] if w['code'] == 'IMPORT_KB_ONLY'], [])
        self.assertEqual([name for name in self.library_part if reads[name]], [])
        self.assert_not_switched()
        # Restarted onto the new knowledge base, then back.
        self.assertEqual([command for _, command in self.engine_commands()],
                         [('restart', 'd32core'), ('check', 'd32core'), ('restart', 'd32core'), ('check', 'd32core')])
        self.assertEqual(self.messages[-1], 'the import of disc 2 stopped: see Import Report')

    def test_library_check_failure_keeps_the_new_knowledge_base_and_a_new_import_finishes(self):
        result, _ = self.run_import(D32_TEST_FAULTS='check:lib-encyclopedia')
        self.assertEqual(result, 2)
        self.assert_failed_with_kb_in_use([('IMPORT_CHECK', 'check', 'lib-encyclopedia', None)])
        # The Library's pointers went back (there was no Library before), and its new generations are gone.
        self.assert_kb_only()
        self.assertEqual(sorted(p.name for p in self.paths.cr.iterdir()), ['.kb-test-kb', 'kb'])
        self.assertEqual(list(self.paths.packs.rglob('INFO')), [])
        first = self.first_library_read()
        self.assertTrue(all(index < first for index, _ in self.engine_commands()))
        self.assertIn(('command', 'rcctl', 'restart', 'kiwix_serve'), self.events[first:])
        # Importing the disc again installs the Library; the knowledge base is already in use.
        self.events.clear()
        result, reads = self.run_import()
        self.assertEqual(result, 0)
        self.assert_installed()
        self.assertEqual(self.engine_commands(), [])
        self.assertEqual(self.report()['units'][0]['state'], 'current')

    # ---------------------------------------------------------------------------------------------- upgrades

    def older_import(self):
        """Import an older disc 2 fully, then put a newer one (another knowledge base and Library) in the drive."""
        self.assertEqual(self.run_import()[0], 0)
        old = imp_journal.Registry(self.paths).active
        old_generations = sorted(p.name for p in self.paths.cr.iterdir() if p.name.startswith('.'))
        self.make_disc(kb_name='kb-b', kb_fingerprint=OTHER_FINGERPRINT, generation='g2', salt=b'newer\n')
        self.events.clear()
        self.messages.clear()
        return old, old_generations

    def test_upgrade_keeps_the_old_library_in_use_until_the_new_one_switches(self):
        old, old_generations = self.older_import()
        result, _ = self.run_import(D32_TEST_SIGNAL_AT='CP-library-copied')
        self.assertEqual(result, 4)
        # The newer knowledge base is in use with the older Library; the way back is the older pair.
        registry = self.assert_kb_only(library={name: old['pointers'][name] for name in LIBRARY_POINTERS})
        self.assertEqual(registry.previous, old)
        for name in old_generations:
            self.assertTrue((self.paths.cr / name).is_dir(), name)
        result, _ = self.run_import('--resume')
        self.assertEqual(result, 0)
        report = self.assert_installed()
        registry = imp_journal.Registry(self.paths)
        self.assertEqual(registry.previous, old)
        for name in old_generations:
            self.assertTrue((self.paths.cr / name).is_dir(), name)
        self.assertEqual(report['rollback']['kb_generation'], 'test-kb')
        self.assertEqual(report['kb']['previous'], {'generation': 'test-kb', 'fingerprint': KB_FINGERPRINT})
        self.assertIn('IMPORT_OLD_KEPT', [w['code'] for w in report['warnings']])

    def test_upgrade_library_failure_keeps_the_old_library_and_the_new_knowledge_base(self):
        old, old_generations = self.older_import()
        tampered = self.encyclopedia_parts[0]
        path = self.disc.joinpath(*tampered.split('/'))
        path.write_bytes(b'X' + path.read_bytes()[1:])
        result, _ = self.run_import()
        self.assertEqual(result, 2)
        self.assert_failed_with_kb_in_use([('IMPORT_FILE_HASH', 'library', 'lib-encyclopedia', tampered)])
        registry = self.assert_kb_only(library={name: old['pointers'][name] for name in LIBRARY_POINTERS})
        self.assertEqual(registry.previous, old)
        for name in old_generations:
            self.assertTrue((self.paths.cr / name).is_dir(), name)
        self.assertEqual(sorted(p.name for p in self.paths.cr.iterdir() if p.name.startswith('.')),
                         sorted(old_generations + ['.kb-kb-b']))
        # The older Library's pages are still the ones its records describe.
        for pack, generation in (('lib-encyclopedia', 'g1'), ('lib-ietf', 'g1')):
            self.assertTrue((self.paths.packs / pack / generation / 'INFO').is_file())

    def test_upgrade_power_cut_in_the_library_switch_finishes_it_with_the_older_pair_kept(self):
        old, _ = self.older_import()
        with mock.patch.object(imp_switch, '_commit_registry', side_effect=_PowerCut),                 self.assertRaises(_PowerCut):
            self.run_import()
        journal = self.journal()
        self.assertEqual((journal['switch']['phase'], journal['outcome'], journal['kb_live']['way_back']),
                         ('done', None, old))
        # The pointers moved; the registry still has the newer knowledge base with the older Library.
        self.assertEqual(self.pointers(), {'kb': self.kb_value(), **self.library_values()})
        self.assertTrue(imp_journal.Registry(self.paths).active['library_pending'])
        self.events.clear()
        result, reads = self.run_import()
        self.assertEqual(result, 0)
        self.assertEqual(reads, Counter())
        self.assert_installed()
        self.assertEqual(imp_journal.Registry(self.paths).previous, old)
        self.assertEqual(self.engine_commands(), [])

    def test_power_cut_in_the_library_rollback_finishes_it_and_keeps_the_knowledge_base(self):
        real = imp_switch._services

        def services(ctx, restoring=False):
            if restoring:
                raise _PowerCut()
            return real(ctx, restoring)
        with mock.patch.object(imp_switch, '_services', services), self.assertRaises(_PowerCut):
            self.run_import(D32_TEST_FAULTS='check:lib-encyclopedia')
        self.assertEqual(self.journal()['switch']['phase'], 'rollback-begun')
        self.messages.clear()
        result, reads = self.run_import()
        self.assertEqual(result, 2)
        self.assertEqual(reads, Counter())
        self.assert_failed_with_kb_in_use([('IMPORT_CHECK', 'check', 'lib-encyclopedia', None)])
        self.assert_kb_only()
        self.assertEqual(sorted(p.name for p in self.paths.cr.iterdir()), ['.kb-test-kb', 'kb'])

    # ---------------------------------------------------------------------------------------------- the report

    def test_report_records_each_units_copy_and_install_times(self):
        self.assertEqual(self.run_import()[0], 0)
        report = self.assert_installed()
        units = {row['id']: row for row in report['units']}
        for unit in ('kb', 'lib-encyclopedia', 'lib-ietf', 'cisco-public'):
            times = [units[unit][key] for key in imp_files.UNIT_TIMES]
            self.assertTrue(all(times), unit)
            self.assertEqual(times, sorted(times), unit)
        self.assertEqual([units['block-rfc'][key] for key in imp_files.UNIT_TIMES], [None] * 4)
        # The knowledge base was in use before any Library pack was read.
        for unit in ('lib-encyclopedia', 'lib-ietf', 'cisco-public'):
            self.assertLessEqual(units['kb']['install_finished'], units[unit]['copy_started'])
        # The checker refuses a time that is not one.
        units['lib-ietf']['install_started'] = 'yesterday'
        self.assertIn('units[2].install_started is not a UTC time',
                      UIJSON.check((json.dumps(report, indent=2) + '\n').encode('ascii')))

    def test_console_lines_keep_the_c6_message_rules(self):
        strings = imp_c6.Strings()
        imp_files.check_strings(strings)
        widest = dict(pct=99, files=99999, count=99999, done=999999, total=999999, packs=99, npacks=99,
                      left='99 h 59 min')
        for key in imp_files.STATUS:
            self.assertLessEqual(len(strings.get(key, **widest)), 240, key)
        broken = imp_c6.Strings()
        broken.rows['c6.import.library.eta'] = broken.rows['c6.import.library.eta'] + ' {unknown}'
        with self.assertRaises(ValueError):
            imp_files.check_strings(broken)
        self.assertEqual(imp_files._duration(30), '1 min')
        self.assertEqual(imp_files._duration(3900), '1 h 05 min')


class SearchAndPairingDuringTheLibrary(_Harness):
    """What the rest of the Workstation needs once the knowledge base is in use, while the Library is imported."""

    def test_knowledge_base_ready_lets_the_rest_work_and_a_library_failure_leaves_it(self):
        priority = [0]
        at_first_read, at_switch = [], []
        real_member = imp_files._open_member
        real_switch = imp_switch.run_switch

        def member(root, path):
            if path in self.library_part and not at_first_read:
                at_first_read.append(priority[0])
            return real_member(root, path)

        def switch(ctx):
            at_switch.append(priority[0])
            return real_switch(ctx)
        with mock.patch.object(os, 'getpriority', lambda which, who: priority[0], create=True), \
                mock.patch.object(os, 'setpriority', lambda which, who, value: priority.__setitem__(0, value),
                                  create=True), \
                mock.patch.object(os, 'PRIO_PROCESS', 0, create=True), \
                mock.patch.object(imp_files, '_open_member', member), \
                mock.patch.object(imp_switch, 'run_switch', switch):
            result, _ = self.run_import()
        self.assertEqual(result, 0)
        self.assert_installed()
        # The Library part ran at a lower CPU priority, and the importer's own came back before the switch.
        self.assertEqual((at_first_read, at_switch), ([imp_files.NICE], [0]))
        # Once the knowledge base was ready the importer never stopped or restarted the engine or the gateway,
        # whose services publication, pairing, MCP search and Ask use; only the encyclopedia server, for its pages.
        ready = self.events.index(('c6', 'running', READY))
        self.assertTrue(all(index < ready for index, _ in self.engine_commands()))
        self.assertEqual([event[2:] for event in self.events[ready:] if event[:2] == ('command', 'rcctl')],
                         [('restart', 'kiwix_serve')])
        # Every console line from then on still says the knowledge base is ready, until the import is done.
        lines = [event[2] for event in self.events[ready:] if event[0] == 'c6']
        done = lines.index('finishing')
        self.assertTrue(all(line.startswith('the knowledge base is ready') for line in lines[:done]), lines)

    def test_library_failure_stops_nothing_the_rest_uses(self):
        failing = 'library/cisco-public.sqlite'
        result, _ = self.run_import(D32_TEST_FAULTS='read:' + failing)
        self.assertEqual(result, 2)
        self.assert_failed_with_kb_in_use([('IMPORT_READ', 'library', 'cisco-public', failing)], resumable=True)
        ready = self.events.index(('c6', 'running', READY))
        self.assertEqual([event for event in self.events[ready:] if event[:2] == ('command', 'rcctl')], [])
        self.assertEqual(self.events[-1][:2], ('c6', 'failed'))
        # The importer's lock is gone: Try Again and a new import can run, and nothing else waits on it.
        self.assertFalse(self.paths.lock.exists())

    def test_publication_pairing_and_first_start_do_not_wait_for_the_import(self):
        # None of these reads the importer's lock, journal or stage folder, or waits for the import's console line.
        site = SITE / 'usr/local/d32'
        programs = [site / 'publication.py', site / 'sbin/d32-publish', site / 'sbin/d32-pairdesk',
                    site / 'libexec/d32-pair', site / 'libexec/deep32-pair', site / 'libexec/d32-gateway-enrol',
                    site / 'libexec/d32-forge-enrol', site / 'libexec/d32-setup', site / 'sbin/d32-setup-status',
                    *sorted((site / 'deep32net').glob('*.py'))]
        for program in programs:
            text = program.read_text(encoding='utf-8')
            for needle in ('/var/db/d32/import', 'import/journal', '.import-stage', 'd32-content-import',
                           'stage=import'):
                self.assertNotIn(needle, text, program.name)


class _PowerCut(BaseException):
    """The machine stops here: no handler of the importer runs."""


class _ShortReads:
    """A disc member whose size is right and whose reads end early, as on a damaged disc."""

    def __init__(self, handle, left):
        self.handle, self.left = handle, left

    def fileno(self):
        return self.handle.fileno()

    def read(self, size=-1):
        if self.left <= 0:
            return b''
        data = self.handle.read(self.left if size < 0 else min(size, self.left))
        self.left -= len(data)
        return data

    def close(self):
        self.handle.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
