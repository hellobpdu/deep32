"""Produce names for newly restored installed text (release specification section 15, Q1).

Only visible programme-name bytes change. Comments, identifiers and article
catalogue titles remain unchanged. The driver installs outputs after its ledger.
"""
import base64
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
MANIFEST_SHA256 = '5da7f139f67dc35dbb17605c1b2f55c97df7fe082662c2c1a0054f3e91694ed1'  # sha256 or public signing fingerprint


def produce(source, row):
    if len(source) != row['source_bytes'] or hashlib.sha256(source).hexdigest() != row['source_sha256']:
        raise ValueError('1015: source store bytes differ: ' + row['installed_path'])
    lines = source.splitlines(keepends=True)
    if len(lines) != row['source_lines']:
        raise ValueError('1015: source line count differs')
    changes = {c['line']: c for c in row['changes']}
    if len(changes) != row['changed_lines']:
        raise ValueError('1015: duplicate change or changed count differs')
    after = []
    for number, line in enumerate(lines, 1):
        if number in changes:
            change = changes[number]
            before, new = bytes.fromhex(change['before_hex']), bytes.fromhex(change['after_hex'])
            if line != before or source.count(before) != change['expected'] or before == new:
                raise ValueError('1015: source text guard differs')
            line = new
        after.append(line)
    data = b''.join(after)
    if sum(a != b for a, b in zip(lines, after)) != row['changed_lines']:
        raise ValueError('1015: changed lines differ')
    if len(data) != row['output_bytes'] or hashlib.sha256(data).hexdigest() != row['output_sha256']:
        raise ValueError('1015: output bytes differ')
    return data


def _plain(root, rel):
    if not rel or rel.startswith('/') or ':' in rel or '\\' in rel or any(p in ('', '.', '..') for p in rel.split('/')):
        raise ValueError('1015: unsafe path')
    path = root / rel
    for part in [path, *path.parents]:
        if part == root:
            break
        if part.is_symlink():
            raise ValueError('1015: linked path')
    return path


def apply(tree_dir):
    root = Path(tree_dir)
    encoded = (BASE / 'late-installed-names.json').read_bytes()
    if hashlib.sha256(encoded).hexdigest() != MANIFEST_SHA256:
        raise ValueError('1015: manifest differs')
    manifest = json.loads(encoded)
    if manifest['prestate_path'] != 'library/src/app/records.c':
        raise ValueError('1015: unexpected runtime sentinel')
    sentinel = _plain(root, manifest['prestate_path'])
    if root.is_symlink() or not sentinel.is_file():
        raise FileNotFoundError('1015: runtime sentinel missing or linked')
    data = sentinel.read_bytes()
    if len(data) != manifest['prestate_bytes'] or hashlib.sha256(data).hexdigest() != manifest['prestate_sha256']:
        raise ValueError('1015: runtime sentinel differs')
    prepared, seen = [], set()
    for row in manifest['files']:
        rel = row['target']
        if not rel.startswith('library/name/late/') or rel in seen:
            raise ValueError('1015: invalid or duplicate output')
        seen.add(rel)
        target = _plain(root, rel)
        if target.exists():
            raise FileExistsError('1015: output already exists: ' + rel)
        payload = _plain(BASE, row['source_payload'])
        source = base64.b64decode(payload.read_bytes(), validate=False)
        output = produce(source, row)
        prepared.append((rel, target, output))
    if len(prepared) != manifest['output_count'] or sum(r['changed_lines'] for r in manifest['files']) != manifest['changed_lines']:
        raise ValueError('1015: output totals differ')
    for rel, target, data in prepared:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return [rel for rel, _, _ in prepared]
