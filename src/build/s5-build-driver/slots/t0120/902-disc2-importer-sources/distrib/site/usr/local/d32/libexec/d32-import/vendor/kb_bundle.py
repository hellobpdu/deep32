"""Build and verify portable knowledge-base bundles."""
from __future__ import annotations
import sys
sys.dont_write_bytecode = True
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import argparse
import collections
import contextlib
import datetime
import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import stat
import struct
import subprocess
import uuid
import f32_sse as f32

GEN_RE = r'[a-z0-9][a-z0-9._-]{0,39}'
QUERY_PROMPT = 'Query: {text}'
DOCUMENT_PROMPT = 'Document: {title}\nSection: {section path}\n\n{text}'
BUNDLE_KEYS = set('format format_version generation fingerprint created export_format dense_format engine embedding reranker chunking blocks counts opt_in manifest_sha256 signer'.split())
DENSE_KEYS = set('format generation collection points_count dim dtype byte_order distance row_order vectors points checks stats source'.split())

# Copied from export_generation.py, SHA256
# dcb00608cf53441b6e85ab087e734c925da55f5c32aa863b0dcd36e00a1bbb3e.  # sha256 or public signing fingerprint
POINT_RECORD = struct.Struct("<16s32s32sB15x")
POINT_LAYOUT = {
    "record_bytes": POINT_RECORD.size,
    "fields": [
        {"offset": 0, "bytes": 16, "what": "point id: the UUID's 16 bytes (RFC 4122 order, uuid.UUID(bytes=...))"},
        {"offset": 16, "bytes": 32, "what": "payload chunk_id: the 64 hex digits after chk_, as bytes (zero if none)"},
        {"offset": 48, "bytes": 32, "what": "payload doc_id: the 64 hex digits after doc_, as bytes (zero if none)"},
        {"offset": 80, "bytes": 1, "what": "flags"},
        {"offset": 81, "bytes": 15, "what": "zero"},
    ],
    "flags": {
        "1": "payload has chunk_id",
        "2": "payload has doc_id",
        "4": "rag.db has the chunk",
        "8": "rag.db index_state.dense_indexed = 1 for the chunk",
        "16": "point id = rag_core.dense.point_id_for_chunk(chunk_id)",
        "32": "payload doc_id = rag.db chunks.doc_id",
    },
}

def canonical_json(obj):
    return (json.dumps(obj, indent=1, sort_keys=True, ensure_ascii=True, allow_nan=False) + '\n').encode('ascii')

def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for part in iter(lambda: f.read(1048576), b''):
            h.update(part)
    return h.hexdigest()

def read_json(path):
    return json.loads(Path(path).read_bytes())

def write_json(path, obj):
    Path(path).write_bytes(canonical_json(obj))

def sqlite_ro(path):
    conn = sqlite3.connect(Path(path).resolve().as_uri() + '?mode=ro&immutable=1', uri=True)
    conn.row_factory = sqlite3.Row
    return contextlib.closing(conn)

def fingerprint(shas):
    values = sorted(shas)
    return hashlib.sha256('\n'.join(values).encode()).hexdigest()[:16] if values else None

def has_table(conn, name):
    return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None

def rag_db_stats(path):
    with sqlite_ro(path) as c:
        shas = [r[0] for r in c.execute('SELECT sha256 FROM documents')]
        structure = None
        if all(has_table(c, t) for t in ('doc_sources', 'doc_sections', 'chunk_spans')) and c.execute('SELECT 1 FROM doc_sources LIMIT 1').fetchone():
            structure = {k: c.execute('SELECT count(*) FROM ' + t).fetchone()[0]
                         for k, t in [('documents', 'doc_sources'), ('sections', 'doc_sections')]}
        return {'n_docs': len(shas), 'n_chunks': c.execute('SELECT count(*) FROM chunks').fetchone()[0],
                'corpus_fingerprint': fingerprint(shas),
                'bm25_tables': {t: c.execute('SELECT count(*) FROM ' + t + '_docsize').fetchone()[0]
                                for t in ('chunk_fts', 'chunk_fts_optin') if has_table(c, t + '_docsize')},
                'structure': structure,
                'dense_indexed': c.execute('SELECT count(*) FROM index_state WHERE dense_indexed=1').fetchone()[0] if has_table(c, 'index_state') else None}

def kb_stats(path):
    with sqlite_ro(path) as c:
        names = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {t: c.execute('SELECT count(*) FROM "' + t.replace('"', '""') + '"').fetchone()[0] for t in names}

def member_name_problems(paths):
    problems, seen = [], set()
    for p in paths:
        if not isinstance(p, str) or not p or len(p.encode('utf-8')) > 1024 or any(ord(x) < 33 or ord(x) > 126 or x in '\\:' for x in p) or any(x in ('', '.', '..') for x in p.split('/')):
            problems.append(('name-unsafe', p))
        if isinstance(p, str):
            if p.lower() in seen:
                problems.append(('name-case-collision', p))
            seen.add(p.lower())
    return problems

class Report:
    def __init__(self, verb):
        self.rule_counts = collections.Counter()
        self.data = {'schema': 'deep32.kb-bundle.v1', 'tool': 'kb_bundle', 'tool_version': '1', 'verb': verb,
                     'status': 'ok', 'exit': 0, 'bundle': None, 'checks': [], 'refusals': [],
                     'refusals_total': 0, 'warnings': [], 'counts': {}}

    @property
    def code(self):
        return self.data['exit']

    def fail(self, code, rule, where='', key='', detail='check failed'):
        self.rule_counts[rule] += 1
        if not self.code:
            self.data.update(exit=code, status='refused')
        self.data['refusals_total'] += 1
        if len(self.data['refusals']) < 200:
            self.data['refusals'].append({'rule': rule, 'where': where, 'key': str(key), 'detail': detail})

    def check(self, name, passed, count=None, detail='checked', code=5, rule=None):
        self.data['checks'].append({'id': name, 'status': 'pass' if passed else 'fail', 'count': count, 'detail': detail})
        if not passed:
            self.fail(code, rule or name, detail=detail)

    def warning(self, name, detail):
        self.data['warnings'].append({'id': name, 'detail': detail})

    def not_run(self, name, detail):
        self.data['checks'].append({'id': name, 'status': 'not-run', 'count': None, 'detail': detail})

def scan_tree(directory, report, symlink_rule):
    found = {}
    if directory.is_symlink():
        report.fail(4, symlink_rule)
        return found
    for path in sorted(directory.rglob('*')):
        rel = path.relative_to(directory).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode) or not (stat.S_ISDIR(mode) or stat.S_ISREG(mode)):
            report.fail(4, symlink_rule, key=rel, detail='non-regular member')
        elif stat.S_ISDIR(mode):
            found[rel] = ('dir', None)
        else:
            found[rel] = ('file', (path.stat().st_size, sha(path)))
    return found

def check_names(found, rules, report):
    for rule, name in member_name_problems(list(found)):
        report.fail(4, rule, key=name)
    for name, (kind, value) in found.items():
        if kind == 'file' and value[0] > rules['max_member_bytes']:
            report.fail(4, 'member-too-large', key=name)

def verify_input_files(directory, manifest, rules, report):
    found = scan_tree(directory, report, 'input-symlink')
    check_names(found, rules, report)
    listed = {e['path']: e for e in manifest['files']}
    if len(listed) != len(manifest['files']):
        report.fail(5, 'input-manifest', detail='duplicate entry')
    for name in sorted(set(found) | set(listed)):
        if name == 'MANIFEST.json':
            continue
        item, actual = listed.get(name), found.get(name)
        if item is None or actual is None or actual[0] != item.get('kind') or (actual[0] == 'file' and actual[1] != (item.get('size'), item.get('sha256'))):
            report.fail(5, 'input-manifest', key=name)
    return found

def document_data(directory):
    with sqlite_ro(directory / 'root/sqlite/rag.db') as c:
        docs = [dict(r) for r in c.execute("SELECT *, COALESCE(NULLIF(json_extract(metadata_json, '$.corpus.doc_type'), ''), source_name) AS collection FROM documents")]
        chunks = [dict(r) for r in c.execute('SELECT * FROM chunks ORDER BY doc_id, ordinal')]
        indexed = {r['chunk_id']: dict(r) for r in c.execute('SELECT * FROM index_state')}
    return docs, chunks, indexed

def point_rows(path):
    raw = path.read_bytes()
    if len(raw) % 96:
        raise ValueError('point record size')
    return [{'point_id': str(uuid.UUID(bytes=raw[i:i+16])), 'chunk_id': 'chk_' + raw[i+16:i+48].hex(),
             'doc_id': 'doc_' + raw[i+48:i+80].hex(), 'flags': raw[i+80], 'padding': raw[i+81:i+96]}
            for i in range(0, len(raw), 96)]

def rebuilt_points(chunks, indexed):
    rows = []
    for c in chunks:
        if indexed.get(c['chunk_id'], {}).get('dense_indexed') == 1:
            pid = uuid.uuid5(uuid.NAMESPACE_URL, 'rag-core:dense:v1:' + c['chunk_id'])
            rows.append((pid.bytes, pid.bytes + bytes.fromhex(c['chunk_id'][4:]) + bytes.fromhex(c['doc_id'][4:]) + bytes([63]) + bytes(15)))
    return b''.join(r[1] for r in sorted(rows))

def vector_stats(directory, dense):
    norms, groups = [], collections.Counter()
    path = directory / 'dense/vectors.f32'
    for start in range(0, dense['points_count'], 256):
        for v in f32.read_rows(path, dense['dim'], start, min(256, dense['points_count'] - start)):
            s = math.sumprod(v, v) if hasattr(math, 'sumprod') else math.fsum(x*x for x in v)
            norms.append(math.sqrt(s))
            groups[hashlib.sha256(struct.pack('<' + 'f'*len(v), *v)).digest()] += 1
    duplicates = [n for n in groups.values() if n > 1]
    return {'norm_min': min(norms) if norms else None, 'norm_max': max(norms) if norms else None,
            'duplicate_vector_groups': len(duplicates), 'rows_in_duplicate_groups': sum(duplicates)}

def integrity(directory, dense, docs, chunks, indexed, rules, report, deep=True, source_manifest=None, bundle=None):
    root = directory / 'root'
    build = read_json(root / 'BUILD_REPORT.json')
    report.check('input-build-incomplete', build.get('status') == 'complete' and not build.get('failed_steps'))
    report.check('input-format', dense.get('format') == 'deep32-dense/1' and dense.get('dtype') == 'float32' and dense.get('byte_order') == 'little', code=4)
    n, dim = dense.get('points_count'), dense.get('dim')
    if type(n) is not int or type(dim) is not int or n < 0 or dim <= 0:
        report.fail(4, 'input-format', detail='invalid dimensions')
        return
    for key, size in [('vectors', n*dim*4), ('points', n*96)]:
        p = directory / ('dense/vectors.f32' if key == 'vectors' else 'dense/points.bin')
        meta = dense.get(key, {})
        report.check('dense-' + key, p.stat().st_size == size and sha(p) == meta.get('sha256') and meta.get('bytes') == size)
    if (directory / 'dense/points.bin').stat().st_size != n*96:
        return
    points = point_rows(directory / 'dense/points.bin')
    report.check('points-layout', dense.get('points', {}).get('layout') == POINT_LAYOUT)
    ids = [p['point_id'] for p in points]
    report.check('points-flags', all(p['flags'] == 63 and not any(p['padding']) for p in points) and ids == sorted(set(ids)), n)
    report.check('points-uuid', all(p['point_id'] == str(uuid.uuid5(uuid.NAMESPACE_URL, 'rag-core:dense:v1:' + p['chunk_id'])) for p in points), n)
    wanted = {c['chunk_id'] for c in chunks if indexed.get(c['chunk_id'], {}).get('dense_indexed') == 1}
    report.check('points-chunks', {p['chunk_id'] for p in points} == wanted and len(points) == len(wanted), n)
    report.check('points-rebuild', (directory / 'dense/points.bin').read_bytes() == rebuilt_points(chunks, indexed), n)
    missing = len(chunks) - len(wanted)
    if missing:
        report.warning('chunks-without-vector', str(missing))
    if deep and (directory / 'dense/vectors.f32').stat().st_size == n*dim*4:
        bad = 0
        for start in range(0, n, 256):
            for row in f32.read_rows(directory / 'dense/vectors.f32', dim, start, min(256, n-start)):
                if not all(map(math.isfinite, row)) or abs(f32.f32(f32.dot_sse(row, row) - 1.0)) > f32.NORM_TOL:
                    bad += 1
        report.check('unit-length', bad == 0, bad)
    else:
        report.not_run('unit-length', 'use --deep')
    rs, ks = rag_db_stats(root / 'sqlite/rag.db'), kb_stats(root / 'kb/kb.sqlite')
    if source_manifest:
        report.check('counts', rs == source_manifest['counts']['rag_db'] and ks == source_manifest['counts']['kb'] and n == source_manifest['counts']['points'])
    manifest_docs = read_json(root / 'sources/manifest.json')
    fp = fingerprint(d['sha256'] for d in docs)
    fps = [fp, build.get('corpus', {}).get('fingerprint'), fingerprint(e['sha256'] for e in manifest_docs)]
    if source_manifest:
        fps.append(source_manifest.get('corpus_fingerprint'))
    if bundle:
        fps.append(bundle.get('fingerprint'))
    report.check('fingerprint', len(set(fps)) == 1 and fp is not None)
    report.check('documents', {(d['source_id'], d['sha256']) for d in docs} == {(d['doc_key'], d['sha256']) for d in manifest_docs})
    opt_docs = {d['doc_id'] for d in docs if d['collection'] in rules['opt_in_collections']}
    with sqlite_ro(root / 'sqlite/rag.db') as c:
        actual = {r[0] for r in c.execute('SELECT doc_id FROM chunk_fts_optin_docs')}
        opted = {r[0] for r in c.execute('SELECT rowid FROM chunk_fts_optin_docsize')}
        default = {r[0] for r in c.execute('SELECT rowid FROM chunk_fts_docsize')}
        wanted_opt = {r[0] for r in c.execute('SELECT rowid, doc_id FROM chunks') if r[1] in opt_docs}
        wanted_default = {r[0] for r in c.execute('SELECT rowid, doc_id FROM chunks') if r[1] not in opt_docs}
    report.check('opt-in-split', actual == opt_docs and opted == wanted_opt and default == wanted_default)
    retrieval = read_json(root / 'config/retrieval.json')
    report.check('compat-opt-in', set(retrieval.get('opt_in_doc_types', [])) == set(rules['opt_in_collections']), code=7)
    expected_collection = 'rag_dense_' + re.sub(r'[^A-Za-z0-9_-]', '_', directory.name if bundle is None else bundle['generation'])
    if source_manifest:
        expected_collection = 'rag_dense_' + re.sub(r'[^A-Za-z0-9_-]', '_', source_manifest['generation'])
    report.check('collection', dense.get('collection') == expected_collection == read_json(root / 'config/dense.json').get('collection'))
    report.data['counts'].update(rag_db=rs, kb=ks, documents=len(docs), chunks=len(chunks), points=n)

def strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from strings(v)

def row_rules(rows, name, kept, rules, report, where, cut=(), markings=()):
    for i, row in enumerate(rows):
        def refuse(rule):
            report.fail(6, rule, where, i)
        refs = row.get('refs', [])
        if isinstance(refs, str):
            try:
                refs = json.loads(refs)
            except ValueError:
                refs = None
        if name == 'glossary':
            if row.get('category') not in rules['glossary_categories']:
                refuse('glossary-category')
            if not isinstance(refs, list) or not refs:
                refuse('glossary-no-ref')
            if isinstance(refs, list):
                for ref in refs:
                    if not isinstance(ref, str) or not any(re.fullmatch(p, ref) for p in rules['glossary_ref_patterns']):
                        refuse('glossary-ref-form')
                    elif ref.startswith('doc:') and ref[4:] not in kept:
                        refuse('glossary-ref-unkept')
        if name in ('cli', 'feature') and row.get('doc_key') not in kept:
            refuse(name + '-doc-unkept')
        if name == 'iana' and isinstance(refs, list):
            if any(isinstance(ref, str) and ref.startswith('doc:') and ref[4:] not in kept for ref in refs):
                refuse('iana-ref-unkept')
        ref_keys = [r[4:] for r in refs if isinstance(r, str) and r.startswith('doc:')] if isinstance(refs, list) else []
        if row.get('doc_key') in cut or any(k in cut for k in ref_keys):
            refuse('cut-key')
        fields = {'glossary': ('term', 'expansion'), 'cli': ('command',), 'feature': ('feature', 'info')}.get(name, ())
        if any(mark in str(row.get(field, '')) for mark in markings for field in fields):
            refuse('marking')

def load_kb_rows(kb):
    data = {}
    with sqlite_ro(kb) as c:
        for table in ('glossary', 'cli', 'feature', 'iana'):
            rows = [dict(r) for r in c.execute('SELECT * FROM ' + table)]
            for row in rows:
                if 'refs_json' in row:
                    row['refs'] = json.loads(row['refs_json'])
            data[table] = rows
    return data

def absolute_path(value):
    return bool(re.match(r'(?:/[A-Za-z]|[A-Za-z]:[/\\]|~/|~\\)', value))

def provenance_bad(value, prefixes=()):
    # Embedded paths in JSON are examined as string values, never as document text.
    try:
        value = json.loads(value)
    except (ValueError, TypeError):
        pass
    return any((absolute_path(s) and not any(s.startswith(p) for p in prefixes)) or
               (s.startswith('<root>') and ('\\' in s or '..' in s.split('/'))) or
               re.search(r'[A-Za-z]:[/\\]|(?:/Users/|/home/)|\\Users\\', s, re.I)
               for s in strings(value))

def tagged_paths_bad(value, prefixes):
    if isinstance(value, dict):
        for key, item in value.items():
            tag = str(key).lower()
            if 'provenance' in tag and provenance_bad(item):
                return True
            if 'report' in tag and provenance_bad(item, prefixes):
                return True
            if tagged_paths_bad(item, prefixes):
                return True
    elif isinstance(value, list):
        return any(tagged_paths_bad(item, prefixes) for item in value)
    return False

def content(directory, docs, chunks, rules, report, cut=(), markings=(), public=False, prefixes=()):
    root, sources = directory / 'root', directory / 'root/sources'
    entries = read_json(sources / 'manifest.json')
    kept = {d['source_id'] for d in docs}
    for i, entry in enumerate(entries):
        if entry.get('doc_type') not in rules['kept_collections']:
            report.fail(6, 'collection-not-kept', 'manifest', i)
        if set(entry) - set(rules['manifest_entry_keys']):
            report.fail(6, 'manifest-field', 'manifest', i)
        if entry.get('doc_key') in cut:
            report.fail(6, 'cut-key', 'manifest', i)
    for d in docs:
        if d['collection'] not in rules['kept_collections']:
            report.fail(6, 'collection-not-kept', 'documents', d['doc_id'])
        if d['source_id'] in cut:
            report.fail(6, 'cut-key', 'documents', d['doc_id'])
    listed = {e['path'] for e in entries}
    for p in sorted(sources.rglob('*')):
        rel = p.relative_to(sources)
        parts = rel.parts
        if p.is_dir() and (len(parts) != 1 or parts[0] not in rules['kept_collections'] + ['kb', 'pages']):
            report.fail(6, 'sources-unknown-dir', 'sources', parts[0])
        if p.is_file() and p.suffix == '.txt' and len(parts) == 2 and parts[0] in rules['kept_collections'] and rel.as_posix() not in listed:
            report.fail(6, 'source-file-unlisted', 'sources', rel.as_posix())
        if p.is_file() and len(parts) == 2 and parts[0] == 'pages':
            if p.stem not in kept:
                report.fail(6, 'page-map-unkept', 'pages', p.stem)
            if p.stem in cut:
                report.fail(6, 'cut-key', 'pages', p.stem)
    for table, rows in load_kb_rows(root / 'kb/kb.sqlite').items():
        row_rules(rows, table, kept, rules, report, table, cut, markings)
    for name, filename in [('glossary', 'glossary.json'), ('cli', 'cli_index.json'), ('feature', 'feature_history.json'), ('iana', 'iana.json')]:
        p = sources / 'kb' / filename
        if p.exists():
            row_rules(read_json(p), name, kept, rules, report, 'root/sources/kb/' + filename, cut, markings)
    if markings:
        bad = sum(any(m in c['text'] for m in markings) for c in chunks)
        bad += sum(any(m in d['title'] for m in markings) for d in docs)
        for p in sources.rglob('*'):
            if p.is_file() and any(m.encode('utf-8') in p.read_bytes() for m in markings):
                bad += 1
        if bad:
            report.fail(6, 'marking', detail=str(bad) + ' matching records')
    allowed = tuple(rules['allowed_path_prefixes']) + tuple(prefixes)
    path_values = []
    with sqlite_ro(root / 'sqlite/rag.db') as c:
        for table in ('documents', 'assets', 'ingest_runs'):
            columns = [r[1] for r in c.execute('PRAGMA table_info(' + table + ')') if r[1] == 'path' or r[1].endswith('_path')]
            for column in columns:
                path_values.extend(r[0] for r in c.execute('SELECT "' + column + '" FROM ' + table) if isinstance(r[0], str))
        for table in [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'") if not r[0].startswith('chunk_fts')]:
            for col in [r[1] for r in c.execute('PRAGMA table_info("' + table + '")') if 'provenance' in r[1]]:
                count = sum(provenance_bad(r[0]) for r in c.execute('SELECT "' + col + '" FROM "' + table + '"') if r[0])
                if count:
                    report.fail(6, 'T7', table + '.' + col, detail=str(count) + ' invalid provenance records')
            for col in [r[1] for r in c.execute('PRAGMA table_info("' + table + '")') if r[1].endswith('_json') and 'provenance' not in r[1]]:
                bad = 0
                for row in c.execute('SELECT "' + col + '" FROM "' + table + '"'):
                    if not row[0]:
                        continue
                    try:
                        bad += tagged_paths_bad(json.loads(row[0]), allowed)
                    except (TypeError, ValueError):
                        continue
                if bad:
                    report.fail(6, 'T7', table + '.' + col, detail=str(bad) + ' invalid nested provenance or report records')
    paths = [root / 'BUILD_REPORT.json'] + list((root / 'config').glob('*.json')) + list((sources / 'kb').glob('*.meta.json'))
    for p in paths:
        obj = read_json(p)
        path_values.extend(strings(obj))
        if tagged_paths_bad(obj, allowed):
            report.fail(6, 'T7', p.relative_to(root).as_posix())
        if p.name == 'BUILD_REPORT.json' and provenance_bad(obj, allowed):
            report.fail(6, 'T7', 'BUILD_REPORT.json')
    bad = sum(absolute_path(v) and not v.startswith(allowed) for v in path_values)
    if bad:
        if public:
            report.fail(6, 'root-path', detail=str(bad) + ' absolute paths')
        else:
            report.warning('root-path', str(bad) + ' absolute paths')

def valid_version(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', value):
        raise ValueError('version')
    return tuple(map(int, value.split('.')))

def compatibility(compat, dense, chunks, indexed, report):
    if compat.get('format') != 'deep32-kb-compat' or compat.get('format_version') != 1:
        report.fail(7, 'compat-format')
    try:
        if valid_version(compat['engine']['min']) > valid_version(compat['engine']['max']):
            raise ValueError('range')
    except (KeyError, TypeError, ValueError):
        report.fail(7, 'compat-engine-range')
    emb = compat.get('embedding', {})
    for values, keys in [(emb, ('model_id', 'served_name', 'revision', 'pooling')), (compat.get('reranker', {}), ('model_id', 'revision'))]:
        for key in keys:
            s = values.get(key)
            if not isinstance(s, str) or not 1 <= len(s) <= 200 or any(ord(ch) < 32 or ord(ch) > 126 for ch in s):
                report.fail(2, 'usage', detail='invalid compatibility text field')
    if emb.get('query_prompt') != QUERY_PROMPT:
        report.fail(7, 'compat-query-prompt')
    if emb.get('document_prompt') != DOCUMENT_PROMPT:
        report.fail(7, 'compat-document-prompt')
    states = [s for s in indexed.values() if s['dense_indexed'] == 1]
    report.check('compat-embedding', all(s.get('embedding_model') == emb.get('served_name') and s.get('embedding_revision') == emb.get('revision') and s.get('embedding_model') is not None and s.get('embedding_revision') is not None for s in states), code=7)
    report.check('compat-document-prompt', all(c['embedding_text'].startswith('Document: ') and '\nSection: ' in c['embedding_text'] and c['embedding_text'].endswith('\n\n' + c['text']) for c in chunks), code=7)

def by_collection(docs, chunks, rules):
    mapping = {d['doc_id']: d['collection'] for d in docs}
    return {k: {'documents': sum(d['collection'] == k for d in docs), 'chunks': sum(mapping[c['doc_id']] == k for c in chunks)} for k in rules['kept_collections']}

def check_blocks(paths, docs, rules, report, bundle=None):
    result, seen = [], set()
    for p in paths:
        obj = read_json(p)
        name = obj.get('block')
        wanted = {(d['source_id'], d['sha256']) for d in docs if d['collection'] == name}
        got = {(d.get('doc_key'), d.get('sha256')) for d in obj.get('docs', [])}
        good = obj.get('format') == 'deep32-corpus-block' and obj.get('format_version') == 1 and name in rules['kept_collections'] and isinstance(obj.get('version'), str) and bool(obj['version']) and bool(wanted) and wanted == got and len(got) == len(obj.get('docs', [])) and name not in seen
        record = {'name': name, 'version': obj.get('version'), 'sha256': sha(p)}
        if bundle is not None:
            good = good and record in bundle['blocks']
        report.check('compat-blocks', good, code=7)
        seen.add(name)
        result.append(record)
    if bundle is None:
        report.check('compat-blocks', seen == {d['collection'] for d in docs}, code=7)
    return sorted(result, key=lambda r: str(r['name']))

def read_list(path, kind):
    if not path:
        return []
    values = []
    try:
        lines = Path(path).read_text('utf-8').splitlines()
    except (OSError, UnicodeError):
        raise ValueError('unreadable list') from None
    for i, line in enumerate(lines, 1):
        if not line.strip() or line.startswith('#'):
            continue
        if (kind == 'keys' and not re.fullmatch(r'[a-z0-9][a-z0-9._-]*', line)) or (kind == 'markings' and len(line) < 3):
            raise ValueError('invalid list line ' + str(i))
        values.append(line)
    return values

def json_argv(value):
    try:
        obj = json.loads(value)
    except (ValueError, TypeError):
        raise ValueError('bad JSON-ARGV') from None
    if not isinstance(obj, list) or not obj or not all(isinstance(s, str) and s and '\0' not in s for s in obj):
        raise ValueError('bad JSON-ARGV')
    return obj

def run_hook(argv, directory, signer_id):
    substitutions = {'{bundle_json}': str((directory / 'BUNDLE.json').resolve()), '{sig}': str((directory / 'BUNDLE.json.sig').resolve()), '{signer_id}': signer_id or ''}
    args = []
    for arg in argv:
        for key, val in substitutions.items():
            arg = arg.replace(key, val)
        args.append(arg)
    try:
        p = subprocess.run(args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=300, shell=False, env=os.environ.copy())
        # Hook output is untrusted and may hold private inputs. Retain no output.
        return p.returncode
    except (OSError, subprocess.TimeoutExpired):
        return -1

def bundle_shape(b):
    try:
        if set(b) != BUNDLE_KEYS or b['format'] != 'deep32-kb-bundle' or type(b['format_version']) is not int or b['format_version'] != 1:
            return False
        if not re.fullmatch(GEN_RE, b['generation']) or not re.fullmatch('[0-9a-f]{16}', b['fingerprint']) or not re.fullmatch('[0-9a-f]{64}', b['manifest_sha256']):
            return False
        parse_created(b['created'])
        if b['export_format'] != 'deep32-generation-export/1' or b['dense_format'] != 'deep32-dense/1':
            return False
        if set(b['engine']) != {'min', 'max'} or valid_version(b['engine']['min']) > valid_version(b['engine']['max']):
            return False
        e = b['embedding']
        if set(e) != set('model_id served_name revision dimension number_format distance pooling query_prompt document_prompt'.split()) or type(e['dimension']) is not int or e['dimension'] <= 0 or e['number_format'] != 'float32-le' or e['distance'] != 'cosine':
            return False
        for obj, keys in [(e, ('model_id', 'served_name', 'revision', 'pooling')), (b['reranker'], ('model_id', 'revision'))]:
            if any(not isinstance(obj[k], str) or not 1 <= len(obj[k]) <= 200 or any(ord(x)<32 or ord(x)>126 for x in obj[k]) for k in keys):
                return False
        if set(b['reranker']) != {'model_id', 'revision'} or set(b['chunking']) != {'policy', 'version'} or not all(isinstance(v, str) and v for v in b['chunking'].values()):
            return False
        if not isinstance(b['blocks'], list) or b['blocks'] != sorted(b['blocks'], key=lambda x:x['name']) or len({x['name'] for x in b['blocks']}) != len(b['blocks']):
            return False
        if any(set(x) != {'name','version','sha256'} or not isinstance(x['version'], str) or not x['version'] or not re.fullmatch('[0-9a-f]{64}', x['sha256']) for x in b['blocks']):
            return False
        if not isinstance(b['opt_in'], list) or b['opt_in'] != sorted(set(b['opt_in'])):
            return False
        if set(b['counts']) != {'documents','chunks','points','by_collection'} or any(type(b['counts'][k]) is not int or b['counts'][k] < 0 for k in ('documents','chunks','points')):
            return False
        if any(set(x) != {'documents','chunks'} or any(type(v) is not int or v<0 for v in x.values()) for x in b['counts']['by_collection'].values()):
            return False
        return valid_signer(b['signer'])
    except (KeyError, TypeError, ValueError, AttributeError):
        return False

def valid_signer(s):
    if set(s) != {'type','id'}:
        return False
    if s['type'] == 'none':
        return s['id'] is None
    if s['type'] == 'openpgp':
        return isinstance(s['id'], str) and re.fullmatch('[0-9A-F]{40}', s['id']) is not None
    return s['type'] == 'external' and isinstance(s['id'], str) and 1 <= len(s['id']) <= 128 and all(32<=ord(c)<=126 for c in s['id'])

def parse_created(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z', value):
        raise ValueError('invalid created time')
    return int(datetime.datetime.strptime(value, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc).timestamp())

def check_bundle(directory, *, rules=None, allow_unsigned=False, verify_cmd=None, deep=False, blocks=(), cut=(), markings=(), public=False, prefixes=(), expected_generation=None, signature_present_only=False, expectations=None):
    report = Report('check')
    directory = Path(directory)
    rules = rules or read_json(Path(__file__).with_name('kb_rules.json'))
    if not directory.is_dir() or not (directory/'BUNDLE.json').is_file() or not (directory/'MANIFEST.json').is_file():
        report.fail(3, 'input-missing')
        return report
    try:
        # Reject links before reading any member or opening a database.
        found = scan_tree(directory, report, 'input-symlink')
        if report.code:
            return report
        b, m = read_json(directory/'BUNDLE.json'), read_json(directory/'MANIFEST.json')
        for name, obj in [('BUNDLE.json', b), ('MANIFEST.json', m)]:
            report.check('json-not-canonical', (directory/name).read_bytes() == canonical_json(obj), code=4)
        report.check('format-unknown', b.get('format') == 'deep32-kb-bundle' and b.get('format_version') == 1 and m.get('format') == 'deep32-kb-bundle-manifest' and m.get('format_version') == 1, code=4)
        report.check('bundle-keys', bundle_shape(b), code=4)
        report.check('bundle-generation', b.get('generation') == (expected_generation or directory.name), code=4)
        if not bundle_shape(b):
            return report
        report.data['bundle'] = {k:b[k] for k in ('generation','fingerprint','manifest_sha256')}
        report.data['bundle']['bundle_json_sha256'] = sha(directory/'BUNDLE.json')
        report.check('bundle-manifest', sha(directory/'MANIFEST.json') == b['manifest_sha256'])
        listed = {e['path']:e for e in m['files']}
        files = {k:v for k,v in found.items() if v[0]=='file' and k not in ('BUNDLE.json','MANIFEST.json','BUNDLE.json.sig')}
        ok = len(listed)==len(m['files']) and set(listed)==set(files)
        ok = ok and all(files[k][1] == (e['size'],e['sha256']) for k,e in listed.items())
        report.check('bundle-files', ok)
        check_names(found, rules, report)
        # Missing or corrupt files cannot safely feed later semantic checks.
        if not ok:
            for group in ('semantic-integrity','content','compatibility','signature'):
                report.not_run(group,'member integrity failed; unsafe to read further')
            return report
        dense=read_json(directory/'dense/EXPORT.json')
        report.check('dense-keys', set(dense)==DENSE_KEYS and dense.get('dim')==b['embedding']['dimension'])
        docs,chunks,indexed=document_data(directory)
        integrity(directory,dense,docs,chunks,indexed,rules,report,deep,bundle=b)
        for name in ('root/sqlite/rag.db','root/kb/kb.sqlite'):
            with sqlite_ro(directory/name) as c:
                report.check('sqlite-quick-check', [r[0] for r in c.execute('PRAGMA quick_check')]==['ok'])
        rs,ks=rag_db_stats(directory/'root/sqlite/rag.db'),kb_stats(directory/'root/kb/kb.sqlite')
        counts={'documents':len(docs),'chunks':len(chunks),'points':dense['points_count'],'by_collection':by_collection(docs,chunks,rules)}
        report.check('counts',b['counts']==counts and m['counts']['rag_db']==rs and m['counts']['kb']==ks and m['counts']['files']==len(files) and m['counts']['bytes']==sum(v[1][0] for v in files.values()))
        report.check('compat-opt-in',b['opt_in']==sorted(rules['opt_in_collections']),code=7)
        report.check('compat-chunking', b['chunking']==read_json(directory/'root/BUILD_REPORT.json')['chunker'], code=7)
        report.check('compat-blocks', {r['name'] for r in b['blocks']}=={d['collection'] for d in docs},code=7)
        compatibility({'format':'deep32-kb-compat','format_version':1,'engine':b['engine'],'embedding':b['embedding'],'reranker':b['reranker']},dense,chunks,indexed,report)
        check_blocks(blocks,docs,rules,report,b)
        for block in b['blocks']:
            report.data['checks'].append({'id':'block-hash','status':'pass','count':1,'detail':block['name']+' '+block['sha256']})
        content(directory,docs,chunks,rules,report,cut,markings,public,prefixes)
        expectations=expectations or {}
        for option,key in [('expect_embedding_model_id','model_id'),('expect_embedding_served_name','served_name'),('expect_embedding_revision','revision')]:
            if expectations.get(option) is not None:
                report.check('compat-embedding',expectations[option]==b['embedding'][key],code=7)
        if expectations.get('expect_dimension') is not None:
            report.check('compat-dimension',expectations['expect_dimension']==dense['dim'],code=7)
        if expectations.get('engine_version') is not None:
            try:
                good=valid_version(b['engine']['min'])<=valid_version(expectations['engine_version'])<=valid_version(b['engine']['max'])
            except ValueError:
                good=False
            report.check('compat-engine',good,code=7)
        if expectations.get('expect_reranker_revision') not in (None,b['reranker']['revision']):
            report.warning('reranker-revision-differs','serving revision differs')
        signature=directory/'BUNDLE.json.sig'
        present=signature.is_file() and not signature.is_symlink() and 1<=signature.stat().st_size<=65536
        if signature_present_only:
            report.check('signature',present,code=8,rule='signature-bad')
        elif b['signer']['type']=='none' or not present:
            if not allow_unsigned:
                report.fail(8,'signature-unsigned')
            else:
                report.not_run('signature','caller vouches for unsigned input')
        elif verify_cmd:
            report.check('signature',run_hook(verify_cmd,directory,b['signer']['id'])==0,code=8,rule='signature-bad')
        elif allow_unsigned:
            report.not_run('signature','not verified (caller vouches)')
        else:
            report.fail(8,'signature-unverifiable')
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error,OverflowError):
        report.fail(5,'bundle-files',detail='invalid member data')
    return report

def allowed_root(rel,rules):
    p=rel.split('/')
    return (rel in ('BUILD_REPORT.json','sqlite/rag.db','kb/kb.sqlite','sources/manifest.json') or
            len(p)==2 and p[0]=='config' and p[1].endswith('.json') or
            len(p)==3 and p[0]=='sources' and ((p[1] in rules['kept_collections'] and p[2].endswith('.txt')) or p[1]=='kb' and p[2].endswith('.json') or p[1]=='pages' and p[2].endswith('.tsv')))

def write_bundle(a,rules,cut,markings):
    r=Report('write'); source=Path(a.generation_dir); out=Path(a.out)
    if not source.is_dir() or not (source/'MANIFEST.json').is_file():
        r.fail(3,'input-missing');return r
    try:
        m=read_json(source/'MANIFEST.json'); gen=m['generation']
    except (ValueError,KeyError,OSError,TypeError):
        r.fail(4,'input-format');return r
    target=out/str(gen);partial=out/('.'+str(gen)+'.partial')
    if out.resolve().is_relative_to(source.resolve()) or source.resolve().is_relative_to(out.resolve()):
        r.fail(9,'output-inside-input');return r
    if target.exists() or partial.exists():
        r.fail(9,'output-exists');return r
    if not isinstance(gen,str) or not re.fullmatch(GEN_RE,gen) or gen!=source.name or m.get('format')!='deep32-generation-export/1':
        r.fail(4,'input-format');return r
    try:
        found=verify_input_files(source,m,rules,r)
        if r.code:return r
        dense=read_json(source/'dense/EXPORT.json');docs,chunks,indexed=document_data(source)
        integrity(source,dense,docs,chunks,indexed,rules,r,source_manifest=m)
        if r.code:return r
        content(source,docs,chunks,rules,r,cut,markings,a.public,a.allow_path_prefix)
        if r.code:return r
        compat=read_json(a.compat)
        compatibility(compat,dense,chunks,indexed,r)
        if r.code:return r
        blocks=check_blocks(a.block,docs,rules,r)
        if r.code:return r
    except FileNotFoundError:
        r.fail(3,'input-missing');return r
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error):
        r.fail(5,'input-format',detail='invalid source data');return r
    signer={'type':'none','id':None} if a.unsigned else {'type':a.signer_type,'id':a.signer_id}
    if not valid_signer(signer) or not a.unsigned and not a.sign_cmd:
        r.fail(2,'usage',detail='invalid signer');return r
    try:
        partial.mkdir(parents=True)
        copied=[];excluded={}
        for name,(kind,value) in sorted(found.items()):
            if kind!='file' or name=='MANIFEST.json':continue
            keep=(name.startswith('root/') and allowed_root(name[5:],rules) or name in ('dense/vectors.f32','dense/points.bin') or re.fullmatch(r'repo/config/[^/]+\.json',name))
            if keep:
                p=partial/name;p.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source/name,p)
                if sha(p)!=value[1]:raise OSError('copy changed')
                copied.append(name)
            elif name=='dense/EXPORT.json':continue
            else:
                if name.startswith('dense/scroll/'):
                    group='dense/scroll/';why='the export build record, not part of a bundle'
                elif name.startswith('root/'):
                    first=name[5:].split('/')[0]
                    group='root/'+first+'/' if '/' in name[5:] and first not in ('config','sqlite','kb','sources') else name
                    why='not named by contract C3; not read by the serving engine'
                else:
                    group=name;why='not named by contract C3; not read by the serving engine'
                e=excluded.setdefault(group,{'path':group,'files':0,'bytes':0,'why':why});e['files']+=1;e['bytes']+=value[0]
        d={k:dense[k] for k in ('format','generation','collection','points_count','dim','dtype','byte_order','row_order','vectors','points')}
        d['points'] = dict(d['points'], layout=POINT_LAYOUT)
        d.update(distance='Cosine',checks={'rows':dense['points_count'],'rows_with':{text:dense['points_count'] for text in POINT_LAYOUT['flags'].values()},'ids_ascending':True},stats=vector_stats(source,dense),source={'export_json_sha256':sha(source/'dense/EXPORT.json'),'scroll_pages':len(dense['scroll']['pages']),'note':'dense/scroll is the export build record and is not part of a bundle'})
        write_json(partial/'dense/EXPORT.json',d)
        files=[{'path':p.relative_to(partial).as_posix(),'size':p.stat().st_size,'sha256':sha(p)} for p in sorted(partial.rglob('*')) if p.is_file()]
        for e in m.get('excluded',[]):
            path='root/'+e['path'];excluded[path]={'path':path,'files':e.get('files',0),'bytes':e.get('bytes',0),'why':'excluded by the export: '+e['why']}
        manifest={'format':'deep32-kb-bundle-manifest','format_version':1,'generation':gen,'fingerprint':m['corpus_fingerprint'],'files':files,'counts':{'files':len(files),'bytes':sum(e['size'] for e in files),'rag_db':rag_db_stats(source/'root/sqlite/rag.db'),'kb':kb_stats(source/'root/kb/kb.sqlite')},'excluded':sorted(excluded.values(),key=lambda e:e['path']),'source':{'export_manifest_sha256':sha(source/'MANIFEST.json'),'export_json_sha256':sha(source/'dense/EXPORT.json'),'exporter_version':m['exporter']['version'],'scroll_pages':len(dense['scroll']['pages']),'sqlite_version':m['versions']['sqlite'],'qdrant_version':m['versions'].get('qdrant',{}).get('version')},'writer':{'name':'kb_bundle','version':'1','rules_sha256':sha(a.rules),'rules_format_version':rules['format_version']},'content':{'rules':'pass','cut_keys':'pass' if a.cut_keys else 'not-run','markings':'pass' if a.markings else 'not-run','public_paths':'pass' if a.public else 'not-run'}}
        write_json(partial/'MANIFEST.json',manifest)
        embedding=dict(compat['embedding']);embedding.update(dimension=dense['dim'],number_format='float32-le',distance='cosine')
        b={'format':'deep32-kb-bundle','format_version':1,'generation':gen,'fingerprint':m['corpus_fingerprint'],'created':a.created,'export_format':m['format'],'dense_format':dense['format'],'engine':compat['engine'],'embedding':embedding,'reranker':compat['reranker'],'chunking':read_json(source/'root/BUILD_REPORT.json')['chunker'],'blocks':blocks,'counts':{'documents':len(docs),'chunks':len(chunks),'points':dense['points_count'],'by_collection':by_collection(docs,chunks,rules)},'opt_in':sorted(rules['opt_in_collections']),'manifest_sha256':sha(partial/'MANIFEST.json'),'signer':signer}
        write_json(partial/'BUNDLE.json',b)
        if not a.unsigned:
            code=run_hook(json_argv(a.sign_cmd),partial,signer['id'])
            if code:r.fail(8,'sign-hook-failed',detail='hook exit '+str(code))
            sig=partial/'BUNDLE.json.sig'
            if not sig.is_file() or sig.is_symlink() or not 1<=sig.stat().st_size<=65536:r.fail(8,'sign-no-signature')
        if not r.code:
            checked=check_bundle(partial,rules=rules,allow_unsigned=a.unsigned,deep=True,blocks=a.block,cut=cut,markings=markings,public=a.public,prefixes=a.allow_path_prefix,expected_generation=gen,signature_present_only=not a.unsigned)
            r.data['checks'].extend(checked.data['checks']);r.data['warnings'].extend(checked.data['warnings'])
            if checked.code:
                for e in checked.data['refusals']:r.fail(checked.code,e['rule'],e['where'],e['key'],e['detail'])
        if r.code:
            shutil.rmtree(partial);return r
        epoch=parse_created(a.created)
        for p in sorted(partial.rglob('*'),reverse=True)+[partial]:os.utime(p,(epoch,epoch))
        partial.rename(target)
        r.data['bundle']={k:b[k] for k in ('generation','fingerprint','manifest_sha256')};r.data['bundle']['bundle_json_sha256']=sha(target/'BUNDLE.json')
        r.data['counts'].update(files=len(files),bytes=sum(e['size'] for e in files))
    except (OSError,ValueError,KeyError,TypeError):
        if partial.exists():shutil.rmtree(partial)
        r.fail(9,'output-refused',detail='output write failed')
    return r

class Parser(argparse.ArgumentParser):
    def error(self,message):
        raise ValueError('invalid arguments')

def parser():
    p=Parser();sub=p.add_subparsers(dest='verb',required=True)
    for verb in ('write','check','info','rules'):
        v=sub.add_parser(verb)
        v.add_argument('--json',action='store_true');v.add_argument('--report')
        v.add_argument('--rules',default=str(Path(__file__).with_name('kb_rules.json')))
        if verb=='write':
            for opt in ('generation-dir','out','created','compat'):v.add_argument('--'+opt,required=True)
            v.add_argument('--unsigned',action='store_true');v.add_argument('--signer-type',choices=['openpgp','external']);v.add_argument('--signer-id');v.add_argument('--sign-cmd')
        if verb in ('check','info'):v.add_argument('--dir',required=True)
        if verb=='info':v.add_argument('--c5',action='store_true')
        if verb in ('write','check'):
            v.add_argument('--block',action='append',default=[]);v.add_argument('--cut-keys');v.add_argument('--markings');v.add_argument('--public',action='store_true');v.add_argument('--allow-path-prefix',action='append',default=[])
        if verb=='check':
            v.add_argument('--deep',action='store_true');v.add_argument('--allow-unsigned',action='store_true');v.add_argument('--verify-cmd')
            for opt in ('expect-embedding-model-id','expect-embedding-served-name','expect-embedding-revision','engine-version','expect-reranker-revision'):v.add_argument('--'+opt)
            v.add_argument('--expect-dimension',type=int)
        if verb=='rules':v.add_argument('--kb-dir',required=True);v.add_argument('--documents',required=True)
    return p

def emit(report,a):
    data=report.data
    if getattr(a,'report',None):
        Path(a.report).parent.mkdir(parents=True,exist_ok=True);write_json(a.report,data)
    stream=sys.stderr if getattr(a,'json',False) else sys.stdout
    for c in data['checks']:
        print('kb_bundle: '+c['id']+': '+{'pass':'pass','fail':'FAIL','not-run':'not-run'}[c['status']]+' '+c['detail'],file=stream)
    if report.code:
        first=data['refusals'][0];print(f"kb_bundle: {data['verb']} REFUSED (exit {report.code}) {first['rule']}: {first['detail']}",file=stream)
    elif data['verb']=='write':
        b=data['bundle'];n=data['counts'];print(f"kb_bundle: write OK {b['generation']} fingerprint {b['fingerprint']} documents {n['documents']} chunks {n['chunks']} points {n['points']} files {n['files']} bytes {n['bytes']}",file=stream)
    else:print('kb_bundle: '+data['verb']+' OK',file=stream)
    if getattr(a,'json',False):print(json.dumps(data,separators=(',',':'),ensure_ascii=True))

def main(argv=None):
    a=None
    try:
        a=parser().parse_args(argv)
        if a.verb=='write':
            parse_created(a.created)
            if a.unsigned == bool(a.signer_type) or a.unsigned and (a.signer_id or a.sign_cmd):raise ValueError('invalid signing arguments')
            if a.sign_cmd:json_argv(a.sign_cmd)
        if getattr(a,'verify_cmd',None):json_argv(a.verify_cmd)
        cut=read_list(getattr(a,'cut_keys',None),'keys');marks=read_list(getattr(a,'markings',None),'markings')
        rules=read_json(a.rules)
        if a.verb=='write':r=write_bundle(a,rules,cut,marks)
        elif a.verb=='check':r=check_bundle(a.dir,rules=rules,allow_unsigned=a.allow_unsigned,verify_cmd=json_argv(a.verify_cmd) if a.verify_cmd else None,deep=a.deep,blocks=a.block,cut=cut,markings=marks,public=a.public,prefixes=a.allow_path_prefix,expectations=vars(a))
        elif a.verb=='info':
            r=Report('info');b=read_json(Path(a.dir)/'BUNDLE.json')
            if b.get('format')!='deep32-kb-bundle' or b.get('format_version')!=1:r.fail(4,'format-unknown')
            else:
                r.data['bundle']={k:b[k] for k in ('generation','fingerprint','manifest_sha256')};r.data['bundle']['bundle_json_sha256']=None
                r.not_run('integrity','info does not verify; run check')
                if a.c5:
                    print('kb_generation='+b['generation']);print('kb_fingerprint='+b['fingerprint']);return 0
        else:
            r=Report('rules');entries=read_json(a.documents);kept={e['doc_key'] for e in entries if e['doc_type'] in rules['kept_collections']};row_counts={};categories=collections.Counter();platforms=collections.Counter()
            for table,name in [('glossary','glossary.json'),('cli','cli_index.json'),('feature','feature_history.json'),('iana','iana.json')]:
                rows=read_json(Path(a.kb_dir)/name);row_counts[name]=len(rows)
                for row in rows:
                    before=r.data['refusals_total'];row_rules([row],table,kept,rules,r,name)
                    delta=r.data['refusals_total']-before
                    if delta and table=='glossary':categories[row.get('category','')]+=delta
                    if delta and table=='cli':platforms[row.get('platform','')]+=delta
            r.data['counts']={'rows':row_counts,'violations':dict(r.rule_counts),'glossary_by_category':dict(categories),'cli_by_platform':dict(platforms)}
        emit(r,a);return r.code
    except (ValueError,TypeError) as e:
        r=Report(getattr(a,'verb','unknown'));r.fail(2,'usage',detail='invalid arguments or list input');emit(r,a);return 2
    except (OSError,KeyError,json.JSONDecodeError):
        r=Report(getattr(a,'verb','unknown'));r.fail(3,'input-missing');emit(r,a);return 3
    except Exception:
        print('kb_bundle: internal error',file=sys.stderr);return 1

if __name__=='__main__':
    raise SystemExit(main())
