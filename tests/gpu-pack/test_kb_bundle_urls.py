"""Provenance URL and Windows path regression checks (offline)."""
import os.path
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kit_support  # noqa: E402,F401  src/gpu-pack and the shared helpers (kit_support.py)

import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import sqlite3
import struct
import sys
import tempfile
import unittest


def candidates():
    explicit = os.environ.get('D32_KB_URL_CANDIDATES')
    if explicit:
        return [Path(value) for value in json.loads(explicit)]
    base = Path(__file__).resolve().parents[2] / 'src' / 'gpu-pack'
    names = ['kb-bundle/kb_bundle.py', 'vendor/kb-bundle/kb_bundle.py',
             'disc2/vendor/t0114/kb-bundle/kb_bundle.py',
             'containers/builder/kb-bundle/kb_bundle.py', 'import/vendor/kb_bundle.py',
             'tree/distrib/site/usr/local/d32/libexec/d32-import/vendor/kb_bundle.py',
             'forge/opt/deep32-forge/import/vendor/kb_bundle.py']
    found = [base / name for name in names if (base / name).is_file()]
    if not found:
        raise RuntimeError('No bundle implementation found for the URL regression tests')
    return found


def load(path):
    name = 'kb_url_' + hashlib.sha256(str(path).encode()).hexdigest()[:12]
    sys.path.insert(0, str(path.parent))
    try:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def build_fixture(base, module):
    """Build a small, synthetic export using the bundled format definitions."""
    generation = 'mini-20261114'
    source = base / 'out/mini-gen' / generation
    source.mkdir(parents=True)

    def write_json(relative, value):
        path = source / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        module.write_json(path, value)

    rules = module.read_json(Path(module.__file__).with_name('kb_rules.json'))
    served_name, revision = 'fixture-embedding', 'fixture-v1'
    documents, chunks, indexed, entries = [], [], {}, []
    for kind in ('rfc', 'powershell'):
        text = 'Synthetic ' + kind + ' provenance URL fixture.\n'
        digest = hashlib.sha256(text.encode('ascii')).hexdigest()
        doc_id, chunk_id = 'doc_' + digest, 'chk_' + digest
        key = 'fixture-' + kind
        relative = kind + '/' + key + '.txt'
        path = source / 'root/sources' / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode('ascii'))
        documents.append((doc_id, key, digest, key, json.dumps({'corpus': {'doc_type': kind}}), kind))
        chunks.append({'chunk_id': chunk_id, 'doc_id': doc_id, 'ordinal': 0, 'text': text,
                       'embedding_text': 'Document: ' + key + '\nSection: fixture\n\n' + text})
        indexed[chunk_id] = {'dense_indexed': 1, 'embedding_model': served_name,
                             'embedding_revision': revision}
        entries.append({'doc_key': key, 'doc_type': kind, 'path': relative, 'sha256': digest})
        block = base / 'fixture/blocks' / generation / kind / 'block.json'
        block.parent.mkdir(parents=True, exist_ok=True)
        module.write_json(block, {'format': 'deep32-corpus-block', 'format_version': 1,
                                 'block': kind, 'version': 'fixture-v1',
                                 'docs': [{'doc_key': key, 'sha256': digest}]})
    rag_path = source / 'root/sqlite/rag.db'
    rag_path.parent.mkdir(parents=True)
    with contextlib.closing(sqlite3.connect(rag_path)) as db, db:
        db.executescript('''
            CREATE TABLE documents (doc_id TEXT, source_id TEXT, sha256 TEXT,
                                    title TEXT, metadata_json TEXT, source_name TEXT);
            CREATE TABLE chunks (chunk_id TEXT, doc_id TEXT, ordinal INTEGER,
                                 text TEXT, embedding_text TEXT);
            CREATE TABLE index_state (chunk_id TEXT, dense_indexed INTEGER,
                                      embedding_model TEXT, embedding_revision TEXT);
            CREATE TABLE chunk_fts_docsize (rowid INTEGER PRIMARY KEY);
            CREATE TABLE chunk_fts_optin_docsize (rowid INTEGER PRIMARY KEY);
            CREATE TABLE chunk_fts_optin_docs (doc_id TEXT);
        ''')
        db.executemany('INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?)', documents)
        db.executemany('INSERT INTO chunks VALUES (?, ?, ?, ?, ?)',
                       [tuple(c[k] for k in ('chunk_id', 'doc_id', 'ordinal', 'text', 'embedding_text'))
                        for c in chunks])
        db.executemany('INSERT INTO index_state VALUES (?, ?, ?, ?)',
                       [(key, 1, served_name, revision) for key in indexed])
        db.execute('INSERT INTO chunk_fts_docsize VALUES (1)')
        db.execute('INSERT INTO chunk_fts_optin_docsize VALUES (2)')
        db.execute('INSERT INTO chunk_fts_optin_docs VALUES (?)', (documents[1][0],))
    kb_path = source / 'root/kb/kb.sqlite'
    kb_path.parent.mkdir(parents=True)
    with contextlib.closing(sqlite3.connect(kb_path)) as db, db:
        for table in ('glossary', 'cli', 'feature', 'iana'):
            db.execute('CREATE TABLE ' + table + ' (refs_json TEXT)')
    fingerprint = module.fingerprint(entry['sha256'] for entry in entries)
    collection = 'rag_dense_' + generation
    write_json('root/sources/manifest.json', entries)
    write_json('root/config/dense.json', {'collection': collection})
    write_json('root/config/retrieval.json', {'opt_in_doc_types': rules['opt_in_collections']})
    write_json('root/BUILD_REPORT.json', {'status': 'complete', 'failed_steps': [],
                                        'corpus': {'fingerprint': fingerprint},
                                        'chunker': {'policy': 'fixture', 'version': '1'}})
    dense_dir = source / 'dense'
    dense_dir.mkdir()
    (dense_dir / 'vectors.f32').write_bytes(struct.pack('<4f', 1.0, 0.0, 0.0, 1.0))
    (dense_dir / 'points.bin').write_bytes(module.rebuilt_points(chunks, indexed))
    write_json('dense/EXPORT.json', {
        'format': 'deep32-dense/1', 'generation': generation, 'collection': collection,
        'points_count': 2, 'dim': 2, 'dtype': 'float32', 'byte_order': 'little',
        'row_order': 'point_id-ascending', 'scroll': {'pages': []},
        'vectors': {'bytes': 16, 'sha256': module.sha(dense_dir / 'vectors.f32')},
        'points': {'bytes': 192, 'sha256': module.sha(dense_dir / 'points.bin'),
                   'layout': module.POINT_LAYOUT}})
    files = []
    for path in sorted(source.rglob('*')):
        entry = {'path': path.relative_to(source).as_posix(),
                 'kind': 'dir' if path.is_dir() else 'file'}
        if path.is_file():
            entry.update(size=path.stat().st_size, sha256=module.sha(path))
        files.append(entry)
    write_json('MANIFEST.json', {
        'format': 'deep32-generation-export/1', 'generation': generation,
        'corpus_fingerprint': fingerprint, 'files': files,
        'counts': {'rag_db': module.rag_db_stats(rag_path), 'kb': module.kb_stats(kb_path), 'points': 2},
        'exporter': {'version': 'fixture-v1'}, 'versions': {'sqlite': sqlite3.sqlite_version}})
    module.write_json(base / 'fixture/compat-fixture.json', {
        'format': 'deep32-kb-compat', 'format_version': 1,
        'engine': {'min': '1.0.0', 'max': '1.0.0'},
        'embedding': {'model_id': 'fixture-model', 'served_name': served_name,
                      'revision': revision, 'pooling': 'mean',
                      'query_prompt': module.QUERY_PROMPT, 'document_prompt': module.DOCUMENT_PROMPT},
        'reranker': {'model_id': 'fixture-reranker', 'revision': 'fixture-v1'}})


class ProvenanceUrls(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.modules = [(path, load(path)) for path in candidates()]

    def check_value(self, value, bad):
        for path, module in self.modules:
            with self.subTest(copy=path.as_posix(), value=value):
                self.assertEqual(bool(module.provenance_bad(value)), bad)
                self.assertEqual(bool(module.provenance_bad({'provenance': value})), bad)

    def test_http(self):
        self.check_value('http://example.invalid/source', False)

    def test_https(self):
        self.check_value('https://example.invalid/source', False)

    def test_ftp(self):
        self.check_value('ftp://example.invalid/source', False)

    def test_url_port(self):
        self.check_value('--rerank-url http://127.0.0.1:8082/v1/rerank', False)

    def test_windows_paths(self):
        for value in ['C:\\x', 'c:/x', 'x D:/y', '(E:\\y)']:
            self.check_value(value, True)

    def test_root_parent_refuses(self):
        self.check_value('<root>/../x', True)

    def test_other_private_paths_refuse(self):
        for value in ['/Users/' + 'example/x', '/home/' + 'example/x', 'x \\Users\\example\\x', '<root>\\x']:
            self.check_value(value, True)

    def test_scheme_boundaries(self):
        for value in ['abc:/x', 'a+b:/x', 'a-b:/x', 'a.b:/x', 'a1b:/x']:
            self.check_value(value, False)

    def test_build_report_write_and_deep_public_check(self):
        with tempfile.TemporaryDirectory(prefix='url-fixture-') as name:
            override = os.environ.get('D32_KB_URL_FIXTURE_ROOT')
            base = Path(override) if override else Path(name)
            if not override:
                build_fixture(base, self.modules[0][1])
            self.check_build_report_fixture(base)

    def test_table_and_column_names_are_quoted_in_the_deep_check(self):
        # A table whose name holds a double quote, with a _json column: the deep check's PRAGMA and SELECT
        # statements must quote both names (Bandit B608; before alpha.3 this raised an SQLite syntax error).
        with tempfile.TemporaryDirectory(prefix='odd-fixture-') as name:
            base = Path(name)
            module = self.modules[0][1]
            build_fixture(base, module)
            source = base / 'out/mini-gen/mini-20261114'
            rag = source / 'root/sqlite/rag.db'
            with contextlib.closing(sqlite3.connect(rag)) as db, db:
                db.execute('CREATE TABLE "odd""name" (x_json TEXT)')
                db.execute('INSERT INTO "odd""name" VALUES (?)', ('{"synthetic": 1}',))
            manifest_path = source / 'MANIFEST.json'
            manifest = module.read_json(manifest_path)
            for entry in manifest['files']:
                if entry['path'] == 'root/sqlite/rag.db':
                    entry.update(size=rag.stat().st_size, sha256=module.sha(rag))
            module.write_json(manifest_path, manifest)
            self.check_build_report_fixture(base)

    def check_build_report_fixture(self, base):
        fixture = base / 'out/mini-gen/mini-20261114'
        if not fixture.is_dir():
            raise RuntimeError('D32_KB_URL_FIXTURE_ROOT has no offline bundle fixture')
        for path, module in self.modules:
            with self.subTest(copy=path.as_posix()), tempfile.TemporaryDirectory(prefix='url-') as name:
                scratch = Path(name)
                source = scratch / 'input/mini-20261114'
                shutil.copytree(fixture, source)
                report_path = source / 'root/BUILD_REPORT.json'
                report = module.read_json(report_path)
                report.setdefault('config', {})['overrides'] = ['--rerank-url http://127.0.0.1:8082/v1/rerank']
                module.write_json(report_path, report)
                manifest_path = source / 'MANIFEST.json'
                manifest = module.read_json(manifest_path)
                for entry in manifest['files']:
                    if entry['path'] == 'root/BUILD_REPORT.json':
                        entry.update(size=report_path.stat().st_size, sha256=module.sha(report_path))
                module.write_json(manifest_path, manifest)
                block_args = sum((['--block', str(base / 'fixture/blocks/mini-20261114' / kind / 'block.json')]
                                  for kind in ['rfc', 'powershell']), [])
                arguments = ['write', '--generation-dir', str(source), '--out', str(scratch / 'out'),
                             '--created', '2026-11-14T00:00:00Z', '--compat', str(base / 'fixture/compat-fixture.json'),
                             '--unsigned', '--public', *block_args, '--json']
                for argv in [arguments, ['check', '--dir', str(scratch / 'out/mini-20261114'), '--deep', '--public',
                                         '--allow-unsigned', *block_args, '--json']]:
                    output = io.StringIO()
                    with contextlib.redirect_stdout(output), contextlib.redirect_stderr(io.StringIO()):
                        status = module.main(argv)
                    self.assertEqual(status, 0, output.getvalue())


if __name__ == '__main__':
    unittest.main(verbosity=2)
