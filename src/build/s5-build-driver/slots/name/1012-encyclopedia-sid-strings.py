"""Produce the name-cleared imported SID table (release specification section 15, N2).

The build driver installs library/name/strings.tsv after copying ledger assets.
The original Windows-1252 byte stream is preserved for every unchanged row.
"""
import base64
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
MANIFEST_SHA256 = 'a83138893f3547bdcca6b0b9e1a726136869d576ce29568e1cb40638837a39ef'  # sha256 or public signing fingerprint


def produce(source, manifest):
    if hashlib.sha256(source).hexdigest() != manifest['source_sha256']:
        raise ValueError('1012: source store bytes differ')
    lines = source.splitlines(keepends=True)
    if len(lines) != manifest['rows'] or len(source) != manifest['source_bytes']:
        raise ValueError('1012: source row or byte count differs')
    by_sid = {}
    for change in manifest['changes']:
        sid = change['sid']
        if sid in by_sid:
            raise ValueError('1012: duplicate SID change')
        by_sid[sid] = change
    if len(by_sid) != manifest['changed_rows']:
        raise ValueError('1012: changed row count differs')
    found = set()
    after = []
    for number, line in enumerate(lines, 1):
        sid_bytes, tab, rest = line.partition(b'\t')
        if not tab or not line.endswith(b'\n'):
            raise ValueError('1012: malformed source row')
        sid = int(sid_bytes)
        if sid in by_sid:
            if sid in found:
                raise ValueError('1012: duplicate source SID')
            c = by_sid[sid]
            old, new = bytes.fromhex(c['before_hex']), bytes.fromhex(c['after_hex'])
            if c['line'] != number or rest != old + b'\n' or old == new:
                raise ValueError('1012: source SID text or position differs: ' + str(sid))
            line = sid_bytes + b'\t' + new + b'\n'
            found.add(sid)
        after.append(line)
    if found != set(by_sid):
        raise ValueError('1012: source is missing a changed SID')
    data = b''.join(after)
    if len(data) != manifest['output_bytes'] or hashlib.sha256(data).hexdigest() != manifest['output_sha256']:
        raise ValueError('1012: produced table differs')
    if sum(a != b for a, b in zip(lines, after)) != manifest['changed_rows']:
        raise ValueError('1012: changed-line count differs')
    return data


def apply(tree_dir):
    root = Path(tree_dir)
    encoded = (BASE / 'sid-strings-changes.json').read_bytes()
    if hashlib.sha256(encoded).hexdigest() != MANIFEST_SHA256:
        raise ValueError('1012: SID manifest differs')
    manifest = json.loads(encoded)
    if manifest['target'] != 'library/name/strings.tsv':
        raise ValueError('1012: unexpected target')
    if manifest['prestate_path'] != 'library/src/app/records.c':
        raise ValueError('1012: unexpected prestate sentinel')
    sentinel = root / manifest['prestate_path']
    for part in [sentinel, *sentinel.parents]:
        if part == root:
            break
        if part.is_symlink():
            raise ValueError('1012: linked prestate sentinel path')
    if root.is_symlink() or not sentinel.is_file():
        raise FileNotFoundError('1012: runtime prestate sentinel missing or linked')
    original = sentinel.read_bytes()
    if len(original) != manifest['prestate_bytes'] or hashlib.sha256(original).hexdigest() != manifest['prestate_sha256']:
        raise ValueError('1012: runtime prestate sentinel differs')
    source = base64.b64decode((BASE / manifest['source_payload']).read_bytes(), validate=False)
    data = produce(source, manifest)
    target = root / manifest['target']
    for part in [target, *target.parents]:
        if part == root:
            break
        if part.is_symlink():
            raise ValueError('1012: linked target path')
    if target.exists():
        raise FileExistsError('1012: produced target already exists')
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return [manifest['target']]
