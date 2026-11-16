"""Reference the lead's stored re-lettered art (release specification section 15, L1-L2).

This transform writes ASCII references only. The registered produced-assets
hook installs the exact external store bytes after normal ledger copies.
"""
import hashlib
import json
from pathlib import Path

BASE = Path(__file__).resolve().parent
MANIFEST_SHA256 = '9efeca89f086dc417787730d61e230fc5e4a7fdf78490b7c7028d089110d9685'  # sha256 or public signing fingerprint


def plain(root, rel):
    if not rel or rel.startswith('/') or ':' in rel or '\\' in rel or any(p in ('', '.', '..') for p in rel.split('/')):
        raise ValueError('1016: unsafe reference path')
    target = root / rel
    for p in (target, *target.parents):
        if p.is_symlink() or p.is_junction():
            raise ValueError('1016: linked reference path')
        if p == root:
            break
    return target


def apply(tree_dir):
    root = Path(tree_dir)
    encoded = (BASE / 'lead-bitmaps.json').read_bytes()
    if hashlib.sha256(encoded).hexdigest() != MANIFEST_SHA256:
        raise ValueError('1016: bitmap reference manifest differs')
    manifest = json.loads(encoded)
    sentinel = plain(root, manifest['prestate_path'])
    if not sentinel.is_file():
        raise FileNotFoundError('1016: runtime sentinel absent')
    original = sentinel.read_bytes()
    if len(original) != manifest['prestate_bytes'] or hashlib.sha256(original).hexdigest() != manifest['prestate_sha256']:
        raise ValueError('1016: runtime sentinel differs')
    prepared, seen = [], set()
    for row in manifest['files']:
        rel = row['target']
        if not rel.startswith('library/name/lead-bitmaps/') or rel in seen:
            raise ValueError('1016: duplicate or unexpected reference')
        seen.add(rel)
        target = plain(root, rel)
        if target.exists():
            raise FileExistsError('1016: reference already exists')
        data = row['reference_ascii'].encode('ascii')
        if hashlib.sha256(data).hexdigest() != row['reference_sha256']:
            raise ValueError('1016: reference bytes differ')
        prepared.append((rel, target, data))
    if len(prepared) != 7 or manifest['output_count'] != 7:
        raise ValueError('1016: expected exactly seven references')
    for rel, target, data in prepared:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return [rel for rel, _, _ in prepared]
